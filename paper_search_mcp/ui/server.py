# paper_search_mcp/ui/server.py
"""Local browser selection UI server. No MCP dependencies."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import secrets
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..cache import get_search_session as cache_get_search_session
from ..config import get_env
from ..utils import DEFAULT_SAVE_PATH, resolve_save_path
from ..selection_confirmation import (
    consume_selection_confirmation_token,
    create_selection_confirmation_token,
)
from ..engine.jobs import _parse_job_snapshot, progress_subscribe, progress_unsubscribe
from ..engine.parse import (
    SELECTION_SEMANTICS_PARSE, SELECTION_SEMANTICS_DOWNLOAD_ONLY,
    dismiss_parse_prompt_state,
    _codex_app_display_candidates,
    _reindexed_display_candidates,
    _selection_semantics_name, _selection_ui_should_open,
    _selection_surface_policy,
    _workflow_parse_execution_name,
)
from .html_templates import _render_local_selection_html

logger = logging.getLogger(__name__)

LOCAL_PAPER_SELECTION_PATH = "/paper-selection"
LOCAL_PAPER_SELECTION_TOOL = "open_paper_selection_page"
DOWNLOAD_SELECTION_TIMEOUT_SECONDS_ENV = "DOWNLOAD_SELECTION_TIMEOUT_SECONDS"
PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS_ENV = "PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS"
DEFAULT_DOWNLOAD_SELECTION_TIMEOUT_SECONDS = 180

_LOCAL_SELECTION_LOCK = threading.Lock()
_LOCAL_SELECTION_SERVER: Optional[ThreadingHTTPServer] = None
_LOCAL_SELECTION_THREAD: Optional[threading.Thread] = None
_LOCAL_SELECTION_BASE_URL = ""
_LOCAL_SELECTION_PAGES: Dict[str, Dict[str, Any]] = {}
_LOCAL_SELECTION_TOKEN_PAGES: Dict[str, str] = {}


def _download_selection_timeout_seconds(num_papers: int = 0) -> int:
    raw = (
        get_env(
            DOWNLOAD_SELECTION_TIMEOUT_SECONDS_ENV,
            str(DEFAULT_DOWNLOAD_SELECTION_TIMEOUT_SECONDS),
        ).strip()
        or str(DEFAULT_DOWNLOAD_SELECTION_TIMEOUT_SECONDS)
    )
    try:
        base = max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid %s=%r; using %d seconds",
            DOWNLOAD_SELECTION_TIMEOUT_SECONDS_ENV,
            raw,
            DEFAULT_DOWNLOAD_SELECTION_TIMEOUT_SECONDS,
        )
        base = DEFAULT_DOWNLOAD_SELECTION_TIMEOUT_SECONDS

    per_paper_raw = get_env(PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS_ENV, "15").strip() or "15"
    try:
        per_paper = max(0, int(per_paper_raw))
    except ValueError:
        per_paper = 15

    if per_paper <= 0 or num_papers <= 0:
        return base
    return max(base, num_papers * per_paper)


def _download_selection_expires_at(timeout_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()


def _is_download_selection_semantics(value: str) -> bool:
    return _selection_semantics_name(value) in {
        SELECTION_SEMANTICS_DOWNLOAD_ONLY,
        "download_and_parse_selected_only",
    }


def _selection_page_expired(page: Dict[str, Any]) -> bool:
    if page.get("selection_expired"):
        return True
    expires_at = str(page.get("selection_expires_at") or "")
    if not expires_at:
        return False
    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at)
    except ValueError:
        return False


def _expire_selection_page(page: Dict[str, Any], reason: str = "timeout") -> Dict[str, Any]:
    page["selection_expired"] = True
    page["selection_expired_reason"] = reason
    page["confirmation_token"] = ""
    return {
        "status": "selection_expired",
        "terminal": True,
        "reason": reason,
        "message": "Selection expired. No PDFs were downloaded.",
    }


def _selection_indices_for_backend(page: Dict[str, Any], selected_indices: str) -> str:
    mapping = page.get("display_index_map")
    if not isinstance(mapping, dict) or not selected_indices:
        return selected_indices
    try:
        selected: List[int] = []
        for part in str(selected_indices).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_text, end_text = part.split("-", 1)
                start = int(start_text.strip())
                end = int(end_text.strip())
                if end < start:
                    start, end = end, start
                selected.extend(range(start, end + 1))
            else:
                selected.append(int(part))
    except ValueError:
        return selected_indices

    translated: List[str] = []
    for index in selected:
        source = mapping.get(str(index), mapping.get(index, index))
        translated.append(str(source))
    return ",".join(translated)


def _local_selection_page_url(page_id: str) -> str:
    _ensure_local_selection_server()
    return f"{_LOCAL_SELECTION_BASE_URL}{LOCAL_PAPER_SELECTION_PATH}/{page_id}"


def mark_local_selection_page_opened(page_id: str) -> None:
    page = _LOCAL_SELECTION_PAGES.get(page_id)
    if isinstance(page, dict):
        page["opened_at"] = datetime.now(timezone.utc).isoformat()


def _ensure_local_selection_server() -> None:
    global _LOCAL_SELECTION_BASE_URL, _LOCAL_SELECTION_SERVER, _LOCAL_SELECTION_THREAD
    with _LOCAL_SELECTION_LOCK:
        if _LOCAL_SELECTION_SERVER is not None:
            return

        host = get_env("LOCAL_UI_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port_raw = get_env("LOCAL_UI_PORT", "0").strip() or "0"
        try:
            port = int(port_raw)
        except ValueError:
            logger.warning("Invalid PAPER_SEARCH_MCP_LOCAL_UI_PORT=%r; using a random free port", port_raw)
            port = 0

        _LOCAL_SELECTION_SERVER = ThreadingHTTPServer((host, port), _LocalSelectionHandler)
        selected_host, selected_port = _LOCAL_SELECTION_SERVER.server_address[:2]
        if selected_host in {"0.0.0.0", ""}:
            selected_host = "127.0.0.1"
        _LOCAL_SELECTION_BASE_URL = f"http://{selected_host}:{selected_port}"
        _LOCAL_SELECTION_THREAD = threading.Thread(
            target=_LOCAL_SELECTION_SERVER.serve_forever,
            name="paper-search-local-selection-ui",
            daemon=True,
        )
        _LOCAL_SELECTION_THREAD.start()


def _create_local_selection_page(
    *,
    selection_token: str,
    papers: List[Dict[str, Any]],
    save_path: str,
    use_scihub: bool,
    mode: str,
    backend: str,
    force: bool,
    custom_save_path_confirmed: bool = False,
    selection_semantics: str = SELECTION_SEMANTICS_PARSE,
    parse_execution: str = "background",
    force_reopen: bool = False,
) -> Dict[str, Any]:
    existing_page_id = _LOCAL_SELECTION_TOKEN_PAGES.get(selection_token)
    if existing_page_id and not force_reopen:
        existing = _LOCAL_SELECTION_PAGES.get(existing_page_id)
        if isinstance(existing, dict):
            return {
                "page_id": existing_page_id,
                "url": _local_selection_page_url(existing_page_id),
                "selection_timeout_seconds": int(
                    existing.get("selection_timeout_seconds") or 0
                ),
                "selection_expires_at": str(
                    existing.get("selection_expires_at") or ""
                ),
                "reused": True,
                "already_opened": bool(existing.get("opened_at")),
            }

    page_id = secrets.token_urlsafe(16)
    confirmation_token = secrets.token_urlsafe(24)
    semantics = _selection_semantics_name(selection_semantics)
    timeout_seconds = _download_selection_timeout_seconds(len(papers))
    session = cache_get_search_session(selection_token)
    metadata = session.get("metadata", {}) if isinstance(session, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    use_source_index_map = (
        bool(session)
        and metadata.get("selection_session_role") != "display_shortlist"
    )
    display_index_map = (
        {
            str(paper.get("index")): int(
                paper.get("source_index") or paper.get("index") or 0
            )
            for paper in papers
            if isinstance(paper, dict) and paper.get("index")
        }
        if use_source_index_map
        else {}
    )
    _LOCAL_SELECTION_PAGES[page_id] = {
        "selection_token": selection_token,
        "confirmation_token": confirmation_token,
        "papers": papers,
        "display_index_map": display_index_map,
        "save_path": save_path or DEFAULT_SAVE_PATH,
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "custom_save_path_confirmed": bool(custom_save_path_confirmed),
        "selection_semantics": semantics,
        "parse_execution": _workflow_parse_execution_name(parse_execution),
        "selection_timeout_seconds": timeout_seconds if _is_download_selection_semantics(semantics) else 0,
        "selection_expires_at": (
            _download_selection_expires_at(timeout_seconds)
            if _is_download_selection_semantics(semantics)
            else ""
        ),
        "selection_expired": False,
        "opened_at": "",
    }
    _LOCAL_SELECTION_TOKEN_PAGES[selection_token] = page_id
    return {
        "page_id": page_id,
        "url": _local_selection_page_url(page_id),
        "selection_timeout_seconds": timeout_seconds if _is_download_selection_semantics(semantics) else 0,
        "selection_expires_at": (
            _LOCAL_SELECTION_PAGES[page_id].get("selection_expires_at", "")
            if _is_download_selection_semantics(semantics)
            else ""
        ),
        "reused": False,
        "already_opened": False,
    }


async def open_paper_selection_page(
    selection_token: str,
    papers: List[Dict[str, Any]],
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
    selection_semantics: str = SELECTION_SEMANTICS_PARSE,
    parse_execution: str = "background",
    open_browser: bool = True,
    requested_count: int = 0,
    full_total: int = 0,
    force_reopen: bool = False,
) -> Dict[str, Any]:
    """Create a local browser checkbox page for paper selection."""
    requested_count = max(0, int(requested_count or 0))
    full_total = int(full_total or len(papers))
    display_papers = _codex_app_display_candidates(
        papers,
        requested_count=requested_count,
    )
    display_papers = _reindexed_display_candidates(display_papers)
    page = _create_local_selection_page(
        selection_token=selection_token,
        papers=display_papers,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        selection_semantics=selection_semantics,
        parse_execution=parse_execution,
        force_reopen=force_reopen,
    )
    opened = False
    if open_browser and not bool(page.get("already_opened")):
        from ..utils import open_url_in_host
        opened = bool(await asyncio.to_thread(open_url_in_host, page["url"]))
        if opened:
            _LOCAL_SELECTION_PAGES.get(page["page_id"], {})["opened_at"] = datetime.now(
                timezone.utc
            ).isoformat()
    elif bool(page.get("already_opened")):
        opened = True
    host, port = _LOCAL_SELECTION_SERVER.server_address[:2] if _LOCAL_SELECTION_SERVER else ("", 0)
    return {
        "status": "ok",
        "interaction": "local_browser_checkbox",
        "selection_token": selection_token,
        "url": page["url"],
        "page_id": page["page_id"],
        "opened": opened,
        "reused": bool(page.get("reused")),
        "already_opened": bool(page.get("already_opened")),
        "selection_timeout_seconds": int(
            _LOCAL_SELECTION_PAGES.get(page["page_id"], {}).get("selection_timeout_seconds")
            or 0
        ),
        "selection_expires_at": str(
            _LOCAL_SELECTION_PAGES.get(page["page_id"], {}).get("selection_expires_at")
            or ""
        ),
        "server_pid": os.getpid(),
        "local_host": str(host),
        "local_port": int(port or 0),
        "papers": display_papers,
        "total": len(display_papers),
        "display_total": len(display_papers),
        "full_total": full_total,
        "requested_count": requested_count,
        "parse_ready_total": sum(
            1
            for paper in display_papers
            if isinstance(paper, dict) and paper.get("parse_ready") is not False
        ),
        "selection_semantics": _selection_semantics_name(selection_semantics),
        "parse_execution": _workflow_parse_execution_name(parse_execution),
        "message": "Open the URL to select papers with checkboxes.",
    }


class _LocalSelectionHandler(BaseHTTPRequestHandler):
    server_version = "PaperSearchLocalSelection/1.0"

    def do_GET(self) -> None:
        # SSE progress stream (must be checked before other routes)
        job_id = self._page_id_from_path("/api/progress-stream")
        if job_id:
            self._handle_progress_stream(job_id)
            return

        job_id = self._page_id_from_path("/api/parse-job")
        if job_id:
            self._send_json(_parse_job_snapshot(job_id))
            return

        page_id = self._page_id_from_path(LOCAL_PAPER_SELECTION_PATH)
        if not page_id:
            self._send_json({"status": "not_found"}, status=404)
            return
        page = _LOCAL_SELECTION_PAGES.get(page_id)
        if not page:
            self._send_json({"status": "not_found", "page_id": page_id}, status=404)
            return
        self._send_html(_render_local_selection_html(page_id, page))

    def do_POST(self) -> None:
        is_download_selection = False
        page_id = self._page_id_from_path("/api/parse-selection")
        is_parse_downloaded_selection = False
        if not page_id:
            page_id = self._page_id_from_path("/api/parse-downloaded-selection")
            is_parse_downloaded_selection = bool(page_id)
        if not page_id:
            page_id = self._page_id_from_path("/api/download-selection")
            is_download_selection = bool(page_id)
        if not page_id:
            page_id = self._page_id_from_path("/api/parse-prompt-timeout")
            if page_id:
                self._handle_parse_prompt_timeout(page_id)
                return
        if not page_id:
            page_id = self._page_id_from_path("/api/download-selection-timeout")
            if page_id:
                self._handle_download_selection_timeout(page_id)
                return
        if not page_id:
            self._send_json({"status": "not_found"}, status=404)
            return
        page = _LOCAL_SELECTION_PAGES.get(page_id)
        if not page:
            self._send_json({"status": "not_found", "page_id": page_id}, status=404)
            return
        try:
            payload = self._read_json()
            selected_indices = str(payload.get("selected_indices") or "")
            backend_selected_indices = _selection_indices_for_backend(
                page,
                selected_indices,
            ) if not is_parse_downloaded_selection else selected_indices
            confirmation_token = str(payload.get("confirmation_token") or "")
            if is_download_selection and _selection_page_expired(page):
                self._send_json(_expire_selection_page(page), status=410)
                return
            if not confirmation_token or confirmation_token != str(page.get("confirmation_token") or ""):
                self._send_json(
                    {
                        "status": "invalid_confirmation",
                        "message": "Selection confirmation token is missing or invalid.",
                    },
                    status=403,
                )
                return
            page["confirmation_token"] = ""
            if is_parse_downloaded_selection:
                from ..tools.core import _run_download_and_parse_selected_papers

                parse_selection_token = str(
                    payload.get("parse_selection_token")
                    or payload.get("selection_token")
                    or ""
                )
                if not parse_selection_token:
                    self._send_json(
                        {
                            "status": "invalid_selection",
                            "message": "Downloaded parse selection token is missing.",
                        },
                        status=400,
                    )
                    return
                result = asyncio.run(
                    _run_download_and_parse_selected_papers(
                        selection_token=parse_selection_token,
                        selected_indices=backend_selected_indices or "all",
                        save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                        use_scihub=bool(page.get("use_scihub")),
                        mode=page.get("mode", "auto"),
                        backend=page.get("backend", ""),
                        force=bool(page.get("force")),
                        custom_save_path_confirmed=bool(page.get("custom_save_path_confirmed")),
                    )
                )
                if isinstance(result, dict):
                    page["parse_prompt_terminal"] = {
                        "status": "parse_started",
                        "selection_token": parse_selection_token,
                        "message": result.get("message", "MinerU parsing started."),
                    }
            elif is_download_selection:
                from ..tools.orchestration import _run_download_selected_papers

                selection_confirmation = create_selection_confirmation_token(
                    selection_token=page["selection_token"],
                    selected_indices=backend_selected_indices,
                    action="download",
                    save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                )
                if selection_confirmation.get("status") != "ok":
                    self._send_json(selection_confirmation, status=400)
                    return
                confirmed = consume_selection_confirmation_token(
                    selection_token=page["selection_token"],
                    selected_indices=backend_selected_indices,
                    confirmation_token=str(
                        selection_confirmation.get("selection_confirmation_token")
                        or ""
                    ),
                    confirmed_via="local_browser",
                    action="download",
                    save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                )
                if confirmed.get("status") != "confirmed":
                    self._send_json(confirmed, status=403)
                    return
                result = asyncio.run(
                    _run_download_selected_papers(
                        selection_token=page["selection_token"],
                        selected_indices=backend_selected_indices,
                        save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                        use_scihub=bool(page.get("use_scihub")),
                        parse_execution="none",
                        mode=page.get("mode", "auto"),
                        backend=page.get("backend", ""),
                        force=bool(page.get("force")),
                        custom_save_path_confirmed=bool(page.get("custom_save_path_confirmed")),
                        large_batch_selection="never",
                        bypass_large_batch_selection=True,
                        _caller="local_browser_ui",
                    )
                )
                page["confirmation_token"] = secrets.token_urlsafe(24)
                if isinstance(result, dict):
                    result["confirmation_token"] = page["confirmation_token"]
                    prompt = result.get("parse_prompt")
                    if isinstance(prompt, dict):
                        page["last_parse_prompt"] = prompt
            else:
                from ..tools.core import _run_download_and_parse_selected_papers

                result = asyncio.run(
                    _run_download_and_parse_selected_papers(
                        selection_token=page["selection_token"],
                        selected_indices=backend_selected_indices,
                        save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                        use_scihub=bool(page.get("use_scihub")),
                        mode=page.get("mode", "auto"),
                        backend=page.get("backend", ""),
                        force=bool(page.get("force")),
                        custom_save_path_confirmed=bool(page.get("custom_save_path_confirmed")),
                    )
                )
            self._send_json(result)
        except Exception as exc:
            logger.exception("Local paper selection request failed")
            self._send_json({"status": "error", "message": str(exc)}, status=500)

    def _handle_download_selection_timeout(self, page_id: str) -> None:
        page = _LOCAL_SELECTION_PAGES.get(page_id)
        if not page:
            self._send_json({"status": "not_found", "page_id": page_id}, status=404)
            return
        try:
            self._read_json()
            self._send_json(_expire_selection_page(page))
        except Exception as exc:
            logger.exception("Local download selection timeout failed")
            self._send_json({"status": "error", "message": str(exc)}, status=500)

    def _handle_parse_prompt_timeout(self, page_id: str) -> None:
        page = _LOCAL_SELECTION_PAGES.get(page_id)
        if not page:
            self._send_json({"status": "not_found", "page_id": page_id}, status=404)
            return
        try:
            payload = self._read_json()
            existing = page.get("parse_prompt_terminal")
            if isinstance(existing, dict) and str(existing.get("terminal") or "").lower() in {"true", "1"}:
                self._send_json(existing)
                return

            prompt = page.get("last_parse_prompt")
            if not isinstance(prompt, dict):
                prompt = {}
            download_selection_token = str(
                payload.get("download_selection_token")
                or prompt.get("download_selection_token")
                or page.get("selection_token")
                or ""
            )
            result = dismiss_parse_prompt_state(
                download_selection_token,
                prompt_id=str(payload.get("prompt_id") or prompt.get("prompt_id") or ""),
                reason=str(payload.get("reason") or "timeout"),
            )
            page["parse_prompt_terminal"] = result
            page["confirmation_token"] = ""
            self._send_json(result)
        except Exception as exc:
            logger.exception("Local parse prompt timeout failed")
            self._send_json({"status": "error", "message": str(exc)}, status=500)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("local selection ui: " + format, *args)

    def _page_id_from_path(self, prefix: str) -> str:
        path = self.path.split("?", 1)[0].rstrip("/")
        expected = prefix.rstrip("/") + "/"
        if not path.startswith(expected):
            return ""
        return path[len(expected) :]

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("content-length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_progress_stream(self, job_id: str) -> None:
        """Server-Sent Events endpoint for real-time parse job progress."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = progress_subscribe(job_id)
        # Push initial snapshot immediately
        try:
            snapshot = _parse_job_snapshot(job_id)
            self.wfile.write(
                f"data: {json.dumps(snapshot, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
            )
            self.wfile.flush()
        except Exception:
            progress_unsubscribe(job_id, q)
            return

        try:
            while True:
                try:
                    payload = q.get(timeout=15)
                except queue.Empty:
                    # Send heartbeat to keep connection alive
                    try:
                        self.wfile.write(": heartbeat\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except Exception:
                        break
                    continue

                try:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    break

                # Check if job is terminal
                try:
                    data = json.loads(payload)
                    status = str(data.get("status") or "").lower()
                    if status in {"completed", "error", "canceled", "not_found"}:
                        # Send final event and close
                        self.wfile.write(
                            "event: done\ndata: {}\n\n".encode("utf-8")
                        )
                        self.wfile.flush()
                        break
                except Exception:
                    pass
        finally:
            progress_unsubscribe(job_id, q)


async def _attach_local_selection_ui(
    prompt: Dict[str, Any],
    *,
    selection_token: str,
    papers: List[Dict[str, Any]],
    save_path: str,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
    force_open: bool = False,
    selection_semantics: str = SELECTION_SEMANTICS_PARSE,
    parse_execution: str = "background",
) -> Dict[str, Any]:
    surface = _selection_surface_policy(force_open=force_open)
    prompt["selection_surface"] = surface
    if surface.get("surface") != "local_browser":
        return prompt

    try:
        prompt["local_browser"] = await open_paper_selection_page(
            selection_token=selection_token,
            papers=papers,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            selection_semantics=selection_semantics,
            parse_execution=parse_execution,
            open_browser=True,
            requested_count=int(prompt.get("requested_count") or 0),
            full_total=int(prompt.get("full_total") or prompt.get("total") or len(papers)),
        )
        prompt["local_browser"]["selection_surface"] = surface
    except Exception as exc:
        logger.exception("Failed to open local paper selection UI")
        prompt["local_browser"] = {
            "status": "error",
            "message": str(exc),
        }
    return prompt
