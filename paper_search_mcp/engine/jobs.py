# paper_search_mcp/engine/jobs.py
"""
Parse job management functions extracted from server.py.

Pure functions that manage the parse job state machine. No MCP dependencies.

Module-level state:
    _PARSE_JOBS: in-memory dict of active parse jobs
    _PARSE_JOB_LOCK: threading lock for safe mutation
    _CURRENT_PARSE_JOB_ID: contextvar tracking the currently running job
    _PARSE_ITEM_TERMINAL_STATUSES: set of terminal item status strings
    _PROGRESS_SUBSCRIBERS: SSE subscriber queues for real-time progress push
    _PROGRESS_SUBSCRIBERS_LOCK: threading lock for subscriber mutations
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ..cache import get_search_session, read_parse_job, utc_now, write_parse_job

# ---------------------------------------------------------------------------
# Module-level state (mirrors server.py)
# ---------------------------------------------------------------------------
_PARSE_JOBS: Dict[str, Dict[str, Any]] = {}
_PARSE_JOB_LOCK = threading.Lock()
_CURRENT_PARSE_JOB_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "paper_search_current_parse_job_id",
    default="",
)
_PARSE_ITEM_TERMINAL_STATUSES: set[str] = {
    "ok",
    "cached",
    "failed",
    "skipped",
    "download_failed",
    "error",
}

# SSE progress subscribers: job_id -> list of queues
_PROGRESS_SUBSCRIBERS: Dict[str, List[queue.Queue]] = {}
_PROGRESS_SUBSCRIBERS_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers (no side effects)
# ---------------------------------------------------------------------------

def _parse_job_stage_progress(status: str) -> int:
    """Map a parse-item status string to a 0-100 progress percentage."""
    normalized = (status or "").strip().lower()
    if normalized in _PARSE_ITEM_TERMINAL_STATUSES:
        return 100
    return {
        "queued": 5,
        "submitted": 5,
        "preparing": 15,
        "downloading": 35,
        "ready": 45,
        "parsing": 70,
        "batch_parsing": 70,
    }.get(normalized, 0)


def _parse_job_item_stage(status: str) -> str:
    """Map a parse-item status to a human-readable stage name for the UI."""
    normalized = (status or "").strip().lower()
    if normalized in _PARSE_ITEM_TERMINAL_STATUSES:
        if normalized in {"ok", "cached"}:
            return "completed"
        if normalized in {"failed", "download_failed", "error"}:
            return "error"
        return "skipped"
    return {
        "queued": "queued",
        "submitted": "queued",
        "preparing": "preparing",
        "downloading": "downloading",
        "ready": "ready",
        "parsing": "parsing",
        "batch_parsing": "parsing",
    }.get(normalized, "queued")


def _parse_job_result_status(parse_result: Dict[str, Any]) -> str:
    """Extract a terminal status string from a parse result dict."""
    status = str(parse_result.get("status") or "unknown").strip().lower()
    if status in {"ok", "cached", "skipped", "download_failed"}:
        return status
    return "failed"


def _parse_job_item_from_candidate(
    candidate: Dict[str, Any],
    index: int,
    *,
    status: str = "queued",
) -> Dict[str, Any]:
    """Build a parse-job item dict from a paper candidate."""
    return {
        "index": index,
        "title": str(candidate.get("title") or "Untitled"),
        "paper_id": str(candidate.get("paper_id") or ""),
        "doi": str(candidate.get("doi") or ""),
        "source": str(candidate.get("source") or "unknown"),
        "status": status,
        "message": "Waiting to start MinerU parsing.",
        "progress_percent": _parse_job_stage_progress(status),
    }


# ---------------------------------------------------------------------------
# Job progress refresh
# ---------------------------------------------------------------------------

def _refresh_parse_job_progress(job: Dict[str, Any]) -> None:
    """Recompute aggregate progress counters for a parse job in place.

    Adds phase counters (downloading, parsing, etc.) for dual-color
    progress bar rendering in the frontend.
    """
    items = job.get("items")
    if not isinstance(items, list) or not items:
        status = str(job.get("status") or "")
        job["progress_percent"] = 100 if status in {"completed", "error", "canceled"} else 0
        job["completed_items"] = 0
        job["downloading"] = 0
        job["parsing"] = 0
        job["phase_downloading"] = 0
        job["phase_parsing"] = 0
        job["phase_completed"] = 0
        job["phase_error"] = 0
        return

    for item in items:
        if isinstance(item, dict):
            item["progress_percent"] = int(
                item.get("progress_percent")
                or _parse_job_stage_progress(str(item.get("status") or ""))
            )
            item["stage"] = _parse_job_item_stage(str(item.get("status") or ""))

    total = len(items)
    completed = sum(
        1
        for item in items
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in _PARSE_ITEM_TERMINAL_STATUSES
    )
    parsed = sum(
        1
        for item in items
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in {"ok", "cached"}
    )
    skipped = sum(
        1
        for item in items
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() == "skipped"
    )
    failed = sum(
        1
        for item in items
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower()
        in {"failed", "download_failed", "error"}
    )
    # Phase counters for dual-color progress bar
    downloading = sum(
        1 for item in items if isinstance(item, dict)
        and str(item.get("stage") or "") == "downloading"
    )
    parsing = sum(
        1 for item in items if isinstance(item, dict)
        and str(item.get("stage") or "") == "parsing"
    )
    phase_downloading = sum(
        1 for item in items if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in {"downloading", "preparing"}
    )
    phase_ready = sum(
        1 for item in items if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() == "ready"
    )
    phase_parsing = sum(
        1 for item in items if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in {"parsing", "batch_parsing"}
    )
    phase_completed = parsed
    phase_error = failed

    progress = round(
        sum(int(item.get("progress_percent") or 0) for item in items if isinstance(item, dict))
        / total
    )
    job["total"] = total
    job["completed_items"] = completed
    job["parsed"] = parsed
    job["failed"] = failed
    job["skipped"] = skipped
    job["progress_percent"] = max(0, min(100, progress))
    # Phase breakdown for dual-color progress bar
    job["downloading"] = downloading + phase_ready
    job["parsing"] = parsing
    job["phase_downloading"] = phase_downloading + phase_ready
    job["phase_parsing"] = phase_parsing
    job["phase_completed"] = phase_completed
    job["phase_error"] = phase_error


# ---------------------------------------------------------------------------
# SSE progress subscribers (real-time push to browser UIs)
# ---------------------------------------------------------------------------

def _progress_notify(job_id: str, snapshot: Dict[str, Any]) -> None:
    """Push a job snapshot to all SSE subscribers for *job_id*."""
    with _PROGRESS_SUBSCRIBERS_LOCK:
        queues = list(_PROGRESS_SUBSCRIBERS.get(job_id, []))
    if not queues:
        return
    # Build a lightweight SSE data payload
    payload = json.dumps(snapshot, ensure_ascii=False, default=str)
    for q in queues:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # drop for slow clients


def progress_subscribe(job_id: str) -> queue.Queue:
    """Create a subscriber queue for a job's progress events.

    Returns a queue.Queue that will receive JSON-encoded job snapshot strings.
    The caller must call ``progress_unsubscribe`` to clean up.
    """
    q: queue.Queue = queue.Queue(maxsize=64)
    with _PROGRESS_SUBSCRIBERS_LOCK:
        _PROGRESS_SUBSCRIBERS.setdefault(job_id, []).append(q)
    return q


def progress_unsubscribe(job_id: str, q: queue.Queue) -> None:
    """Remove a subscriber queue."""
    with _PROGRESS_SUBSCRIBERS_LOCK:
        queues = _PROGRESS_SUBSCRIBERS.get(job_id, [])
        try:
            queues.remove(q)
        except ValueError:
            pass
        if not queues:
            _PROGRESS_SUBSCRIBERS.pop(job_id, None)


# ---------------------------------------------------------------------------
# Job item mutation
# ---------------------------------------------------------------------------

def _update_parse_job_item(job_id: str, index: int, **updates: Any) -> None:
    """Atomically update a single item within a parse job (in-memory + cache).

    Also enriches each item with a ``stage`` field for UI rendering and
    pushes a progress event to SSE subscribers.
    """
    if not job_id:
        return
    with _PARSE_JOB_LOCK:
        job = _PARSE_JOBS.get(job_id)
        if not job:
            stored = read_parse_job(job_id)
            if not stored:
                return
            items = stored.get("items", [])
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and int(item.get("index") or 0) == int(index):
                    item.update(updates)
                    item["updated_at"] = utc_now()
                    item["stage"] = _parse_job_item_stage(str(item.get("status") or ""))
                    item["progress_percent"] = _parse_job_stage_progress(
                        str(item.get("status") or "")
                    )
                    break
            stored["updated_at"] = utc_now()
            stored["updated_epoch"] = time.time()
            _refresh_parse_job_progress(stored)
            write_parse_job(job_id, stored)
            _progress_notify(job_id, _serializable_parse_job(stored, active=True))
            return

        items = job.get("items", [])
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and int(item.get("index") or 0) == int(index):
                item.update(updates)
                item["updated_at"] = utc_now()
                item["stage"] = _parse_job_item_stage(str(item.get("status") or ""))
                item["progress_percent"] = _parse_job_stage_progress(
                    str(item.get("status") or "")
                )
                if "downloading" in str(item.get("status") or "").lower():
                    item.setdefault("download_started_epoch", time.time())
                elif "parsing" in str(item.get("status") or "").lower():
                    item.setdefault("parse_started_epoch", time.time())
                break
        job["updated_epoch"] = time.time()
        job["updated_at"] = utc_now()
        if updates.get("current"):
            job["current"] = str(updates["current"])
        elif updates.get("message"):
            job["current"] = str(updates["message"])
        _refresh_parse_job_progress(job)
        _persist_parse_job(
            job_id, job, active=job.get("status") not in {"completed", "canceled", "error"}
        )
        _progress_notify(job_id, _serializable_parse_job(job, active=True))


def _update_parse_job_items(job_id: str, indices: List[int], **updates: Any) -> None:
    """Bulk-update multiple items in a parse job."""
    for index in indices:
        _update_parse_job_item(job_id, index, **updates)


# ---------------------------------------------------------------------------
# Job preview
# ---------------------------------------------------------------------------

async def _parse_job_preview_items(
    selection_token: str,
    selected_indices: str,
) -> Tuple[List[Dict[str, Any]], List[int], str]:
    """Build a list of parse-job item dicts from a saved search session.

    Lazy-imports parse helpers from .parse to avoid circular imports.
    """
    from .parse import _paper_parse_candidate, _parse_selected_indices  # noqa: PLC0415

    session = await asyncio.to_thread(get_search_session, selection_token)
    if not session:
        return [], [], "Search session not found."
    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []
    try:
        indices = _parse_selected_indices(selected_indices, len(papers))
    except ValueError as exc:
        return [], [], str(exc)

    items: List[Dict[str, Any]] = []
    for index in indices:
        paper = papers[index - 1] if 0 <= index - 1 < len(papers) else {}
        candidate = _paper_parse_candidate(paper if isinstance(paper, dict) else {}, index)
        status = "queued" if candidate.get("parse_ready") else "skipped"
        item = _parse_job_item_from_candidate(candidate, index, status=status)
        if status == "skipped":
            item["message"] = str(candidate.get("reason") or "Paper is not parse-ready.")
        items.append(item)
    return items, indices, f"Queued {len(items)} paper(s) for MinerU parsing."


# ---------------------------------------------------------------------------
# Job snapshot / serialisation / persistence
# ---------------------------------------------------------------------------

def _parse_job_snapshot(job_id: str) -> Dict[str, Any]:
    """Return a serialisable snapshot of a parse job (in-memory or cached)."""
    with _PARSE_JOB_LOCK:
        job = dict(_PARSE_JOBS.get(job_id) or {})
    if not job:
        stored = read_parse_job(job_id)
        if stored:
            stored.setdefault("active", False)
            return stored
        return {"status": "not_found", "job_id": job_id}
    return _serializable_parse_job(
        job,
        active=job.get("status") not in {"completed", "canceled", "error"},
    )


def _serializable_parse_job(job: Dict[str, Any], *, active: bool) -> Dict[str, Any]:
    """Strip non-serialisable fields (task, thread) from a job dict."""
    snapshot = dict(job)
    snapshot.pop("task", None)
    snapshot.pop("thread", None)
    snapshot["active"] = active
    return snapshot


def _persist_parse_job(job_id: str, job: Dict[str, Any], *, active: bool = False) -> None:
    """Write a serialisable snapshot of a job to the cache."""
    snapshot = _serializable_parse_job(job, active=active)
    write_parse_job(job_id, snapshot)


# ---------------------------------------------------------------------------
# Job-level mutation
# ---------------------------------------------------------------------------

def _update_parse_job(job_id: str, **updates: Any) -> None:
    """Atomically update top-level fields of a parse job and push SSE event."""
    with _PARSE_JOB_LOCK:
        job = _PARSE_JOBS.get(job_id)
        if not job:
            stored = read_parse_job(job_id)
            if stored:
                stored.update(updates)
                stored["updated_at"] = utc_now()
                stored["updated_epoch"] = time.time()
                write_parse_job(job_id, stored)
                _progress_notify(job_id, _serializable_parse_job(stored, active=True))
            return
        job.update(updates)
        job["updated_epoch"] = time.time()
        job["updated_at"] = utc_now()
        _refresh_parse_job_progress(job)
        _persist_parse_job(
            job_id, job, active=job.get("status") not in {"completed", "canceled", "error"}
        )
        _progress_notify(job_id, _serializable_parse_job(job, active=True))


# ---------------------------------------------------------------------------
# Run / thread entry points
# ---------------------------------------------------------------------------

async def _run_parse_job(
    *,
    parse_fn,
    job_id: str,
    selection_token: str,
    selected_indices: str,
    save_path: str,
    use_scihub: bool,
    mode: str,
    backend: str,
    force: bool,
    custom_save_path_confirmed: bool,
) -> None:
    """Run a background parse job, calling *parse_fn* for the actual work.

    *parse_fn* must be an async callable with the same signature as
    ``parse_selected_papers``.  It is injected to avoid circular imports
    from the tools package.
    """
    token = _CURRENT_PARSE_JOB_ID.set(job_id)
    _update_parse_job(
        job_id,
        status="running",
        started_epoch=time.time(),
        started_at=utc_now(),
        current="Starting MinerU parsing.",
        message="MinerU parsing started.",
    )
    try:
        result = await parse_fn(
            selection_token=selection_token,
            selected_indices=selected_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        result_status = (
            str(result.get("status") if isinstance(result, dict) else "").strip().lower()
        )
        job_status = (
            "error"
            if result_status in {"not_found", "invalid_selection", "invalid_request"}
            else "completed"
        )
        if job_status == "completed" and isinstance(result, dict):
            for item_result in result.get("results", []) or []:
                if not isinstance(item_result, dict):
                    continue
                index = int(item_result.get("index") or 0)
                if not index:
                    continue
                _update_parse_job_item(
                    job_id,
                    index,
                    status=str(item_result.get("status") or "ok"),
                    message=str(item_result.get("message") or "MinerU parse finished."),
                    pdf_path=item_result.get("pdf_path", ""),
                    parse=item_result.get("parse"),
                    current=f"Finished paper {index}.",
                )
        _update_parse_job(
            job_id,
            status=job_status,
            completed_epoch=time.time(),
            completed_at=utc_now(),
            result=result,
            parsed=result.get("parsed", 0) if isinstance(result, dict) else 0,
            total=result.get("total", 0) if isinstance(result, dict) else 0,
            progress_percent=100,
            current=(
                "MinerU parsing finished."
                if job_status == "completed"
                else "MinerU parsing could not start."
            ),
            message=(
                "MinerU parsing finished."
                if job_status == "completed"
                else str(
                    result.get("message")
                    if isinstance(result, dict)
                    else "MinerU parsing failed."
                )
            ),
        )
    except asyncio.CancelledError:
        _update_parse_job(
            job_id,
            status="canceled",
            completed_epoch=time.time(),
            completed_at=utc_now(),
            message="Job was canceled.",
            current="MinerU parsing was canceled.",
        )
        raise
    except Exception as exc:
        logger.exception("Parse job %s failed", job_id)
        _update_parse_job(
            job_id,
            status="error",
            completed_epoch=time.time(),
            completed_at=utc_now(),
            message=str(exc),
            current="MinerU parsing failed.",
        )
    finally:
        _CURRENT_PARSE_JOB_ID.reset(token)


def _run_parse_job_thread(
    *,
    parse_fn,
    job_id: str,
    selection_token: str,
    selected_indices: str,
    save_path: str,
    use_scihub: bool,
    mode: str,
    backend: str,
    force: bool,
    custom_save_path_confirmed: bool,
) -> None:
    """Synchronous wrapper that runs ``_run_parse_job`` via ``asyncio.run``."""
    asyncio.run(
        _run_parse_job(
            parse_fn=parse_fn,
            job_id=job_id,
            selection_token=selection_token,
            selected_indices=selected_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
    )
