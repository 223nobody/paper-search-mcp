# paper_search_mcp/tools/core.py
"""
Session, job management, and source diagnostic MCP tools extracted from server.py.

Registered via ``register_core_tools(mcp)``.  The function imports state and
helper functions from the engine sub-packages and the cache layer.  Cross-tool
references (e.g. ``parse_pdf_with_mineru``) use lazy imports to avoid circular
dependencies with other tool modules.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from ..cache import (
    get_search_session as cache_get_search_session,
    delete_search_session as cache_delete_search_session,
    list_parse_job_records,
    list_search_sessions as cache_list_search_sessions,
    read_parse_job,
    utc_now,
    write_parse_job,
)
from ..engine.download import _invalid_mcp_save_path, _resolve_session_paper_pdf
from ..engine.jobs import (
    _PARSE_JOBS,
    _PARSE_JOB_LOCK,
    _CURRENT_PARSE_JOB_ID,
    _PARSE_ITEM_TERMINAL_STATUSES,
    _parse_job_item_from_candidate,
    _parse_job_preview_items,
    _parse_job_result_status,
    _parse_job_snapshot,
    _parse_job_stage_progress,
    _persist_parse_job,
    _refresh_parse_job_progress,
    _run_parse_job_thread,
    _serializable_parse_job,
    _update_parse_job,
    _update_parse_job_item,
    _update_parse_job_items,
)
from ..engine.search import _env_int
from ..engine.parse import (
    _attach_mineru_key_prompt,
    _first_mineru_key_prompt,
    _mineru_batch_parse_enabled,
    _paper_parse_candidate,
    _parse_selected_indices,
    dismiss_parse_prompt_state,
)
from ..engine.search import (
    AGENT_SKILL_BROAD_SOURCES,
    AGENT_SKILL_FAST_SOURCES,
    ALL_SOURCES,
    SEARCH_PROFILES,
    SOURCE_CAPABILITIES,
    _disabled_sources,
    _parse_sources,
    _rank_sources_by_reliability,
    _source_capability_report,
    _source_reliability,
)
from ..parsers.mineru import parse_pdfs_with_mineru as run_parse_pdfs_with_mineru
from ..utils import DEFAULT_SAVE_PATH, resolve_save_path
from ..citation import export_citation as format_citation
from ..citation import export_citations as format_citations

logger = logging.getLogger(__name__)

PARSE_CONCURRENCY_ENV = "PARSE_CONCURRENCY"

_WIDGET_ACCESSIBLE_TOOL_META = {
    "openai/widgetAccessible": True,
    "ui": {"visibility": ["app"]},
}


# ---------------------------------------------------------------------------
# Lazy-import helper for cross-tool references
# ---------------------------------------------------------------------------

async def _lazy_parse_pdf_with_mineru(
    pdf_path: str,
    *,
    paper_key: str = "",
    source: str = "",
    paper_id: str = "",
    doi: str = "",
    title: str = "",
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Call ``parse_pdf_with_mineru`` with its MCP-tool signature.

    Tries to lazy-import the registered tool from the sibling tools package
    first; falls back to the underlying parser + key-prompt wrapper if the
    tool module is not yet extracted.
    """
    # Attempt the lazy import from sibling tools module (forward-looking).
    try:
        from .parse import parse_pdf_with_mineru  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError:
        pass
    else:
        return await parse_pdf_with_mineru(
            pdf_path=pdf_path,
            paper_key=paper_key,
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            mode=mode,
            backend=backend,
            force=force,
        )

    # Fallback: use the underlying parser directly.
    from ..parsers.mineru import parse_pdf_with_mineru as _mineru_parse  # noqa: PLC0415

    result = await asyncio.to_thread(
        _mineru_parse,
        pdf_path,
        paper_key_hint=paper_key,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        mode=mode,
        backend=backend,
        force=force,
    )
    return _attach_mineru_key_prompt(result)


# ===========================================================================
# Module-level parse functions (importable by sibling tools modules)
# ===========================================================================


async def _run_parse_selected_papers(
    selection_token: str,
    selected_indices: str = "all",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
) -> Dict[str, Any]:
    """Parse papers from a saved search session by numbered selection.

    This is the importable module-level function.  The MCP tool
    ``parse_selected_papers`` is a thin wrapper that delegates here.
    """
    invalid_save_path = _invalid_mcp_save_path(
        save_path,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )
    if invalid_save_path:
        return invalid_save_path

    save_path = resolve_save_path(save_path)
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    if not session:
        return {
            "status": "not_found",
            "selection_token": selection_token,
            "message": "Search session not found. Run search_papers_for_parsing again.",
        }

    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []

    try:
        indices = _parse_selected_indices(selected_indices, len(papers))
    except ValueError as exc:
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "message": str(exc),
            "total": len(papers),
        }

    job_id = _CURRENT_PARSE_JOB_ID.get("")
    if job_id:
        _update_parse_job_items(
            job_id,
            indices,
            status="preparing",
            message="Preparing PDF for MinerU parsing.",
            current="Preparing selected PDFs.",
        )

    async def _resolve_selected(index: int) -> Dict[str, Any]:
        if job_id:
            _update_parse_job_item(
                job_id,
                index,
                status="downloading",
                message="Resolving or downloading the PDF.",
                current=f"Resolving paper {index}.",
            )
        paper = papers[index - 1]
        if not isinstance(paper, dict):
            return {
                "index": index,
                "status": "skipped",
                "message": "Stored search result is not a paper dictionary.",
            }
        return await _resolve_session_paper_pdf(
            paper=paper,
            index=index,
            save_path=save_path,
            use_scihub=use_scihub,
        )

    concurrency = _env_int(PARSE_CONCURRENCY_ENV, 3, minimum=1)
    semaphore = asyncio.Semaphore(concurrency)

    async def _limited(index: int) -> Dict[str, Any]:
        async with semaphore:
            return await _resolve_selected(index)

    resolved_results = await asyncio.gather(*[_limited(index) for index in indices])
    if job_id:
        for resolved in resolved_results:
            index = int(resolved.get("index") or 0)
            if not index:
                continue
            if resolved.get("status") == "ready":
                _update_parse_job_item(
                    job_id,
                    index,
                    status="ready",
                    message="PDF is ready. Waiting for MinerU.",
                    pdf_path=resolved.get("pdf_path", ""),
                    download_method=resolved.get("download_method", ""),
                    current=f"PDF ready for paper {index}.",
                )
            else:
                _update_parse_job_item(
                    job_id,
                    index,
                    status=str(resolved.get("status") or "skipped"),
                    message=str(resolved.get("message") or "PDF could not be prepared."),
                    current=f"Skipped paper {index}.",
                )
    ready_results = [result for result in resolved_results if result.get("status") == "ready"]
    results_by_index: Dict[int, Dict[str, Any]] = {
        int(result.get("index") or 0): result
        for result in resolved_results
        if result.get("status") != "ready"
    }

    batch_parse_used = len(ready_results) > 1 and _mineru_batch_parse_enabled(mode)
    if ready_results:
        parse_items = []
        for resolved in ready_results:
            candidate = resolved["candidate"]
            parse_items.append(
                {
                    "index": resolved["index"],
                    "pdf_path": resolved["pdf_path"],
                    "source": candidate["source"],
                    "paper_id": candidate["paper_id"],
                    "doi": candidate["doi"],
                    "title": candidate["title"],
                }
            )

        if batch_parse_used:
            if job_id:
                _update_parse_job_items(
                    job_id,
                    [int(result.get("index") or 0) for result in ready_results],
                    status="batch_parsing",
                    message="MinerU batch parsing is running.",
                    current=f"MinerU batch parsing {len(ready_results)} paper(s).",
                )
            parse_outputs = await asyncio.to_thread(
                run_parse_pdfs_with_mineru,
                parse_items,
                mode=mode,
                backend=backend,
                force=force,
            )
        else:
            parse_semaphore = asyncio.Semaphore(concurrency)

            async def _parse_one(item: Dict[str, Any]) -> Dict[str, Any]:
                async with parse_semaphore:
                    if job_id:
                        _update_parse_job_item(
                            job_id,
                            int(item.get("index") or 0),
                            status="parsing",
                            message="MinerU parsing is running.",
                            current=f"MinerU parsing paper {item.get('index')}.",
                        )
                    return await _lazy_parse_pdf_with_mineru(
                        pdf_path=item["pdf_path"],
                        source=item["source"],
                        paper_id=item["paper_id"],
                        doi=item["doi"],
                        title=item["title"],
                        mode=mode,
                        backend=backend,
                        force=force,
                    )

            parse_outputs = await asyncio.gather(*[_parse_one(item) for item in parse_items])

        for resolved, parse_result in zip(ready_results, parse_outputs):
            candidate = resolved["candidate"]
            parse_result = _attach_mineru_key_prompt(parse_result)
            if job_id:
                result_status = _parse_job_result_status(parse_result)
                _update_parse_job_item(
                    job_id,
                    int(resolved["index"]),
                    status=result_status,
                    message=str(parse_result.get("message") or f"MinerU parse {result_status}."),
                    pdf_path=resolved.get("pdf_path", ""),
                    parse=parse_result,
                    current=f"Finished paper {resolved['index']}.",
                )
            results_by_index[int(resolved["index"])] = {
                "index": resolved["index"],
                "status": parse_result.get("status", "unknown"),
                "candidate": candidate,
                "download_method": resolved["download_method"],
                "pdf_path": resolved["pdf_path"],
                "parse": parse_result,
            }

    results = [results_by_index.get(index, {"index": index, "status": "skipped"}) for index in indices]

    parsed = sum(1 for result in results if result.get("status") in {"ok", "cached"})
    skipped = sum(1 for result in results if result.get("status") == "skipped")
    failed = len(results) - parsed - skipped
    status = "ok" if failed == 0 else "partial" if parsed else "failed"

    summary = {
        "status": status,
        "selection_token": selection_token,
        "query": session.get("query", ""),
        "selected_indices": indices,
        "results": results,
        "total": len(results),
        "parsed": parsed,
        "failed": failed,
        "skipped": skipped,
        "parse_concurrency": concurrency,
        "batch_parse": {
            "attempted": batch_parse_used,
            "ready": len(ready_results),
            "mode": mode or "auto",
        },
    }
    prompt = _first_mineru_key_prompt(results)
    if prompt:
        summary["mineru_api_key_prompt"] = prompt
    return summary


async def _run_submit_parse_job(
    *,
    parse_fn,
    selection_token: str,
    selected_indices: str = "all",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
) -> Dict[str, Any]:
    """Submit parse_selected_papers as a background job and return immediately.

    This is the importable module-level function.  The MCP tool
    ``submit_parse_job`` is a thin wrapper that delegates here.
    """
    invalid_save_path = _invalid_mcp_save_path(
        save_path,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )
    if invalid_save_path:
        return invalid_save_path

    items, parsed_indices, preview_message = await _parse_job_preview_items(
        selection_token, selected_indices
    )

    job_id = f"parse_{int(time.time())}_{secrets.token_hex(6)}"
    created_epoch = time.time()
    created_at = utc_now()
    active_items = [item for item in items if str(item.get("status") or "") != "skipped"]
    worker = threading.Thread(
        target=_run_parse_job_thread,
        kwargs={
            "parse_fn": parse_fn,
            "job_id": job_id,
            "selection_token": selection_token,
            "selected_indices": selected_indices,
            "save_path": save_path,
            "use_scihub": use_scihub,
            "mode": mode,
            "backend": backend,
            "force": force,
            "custom_save_path_confirmed": custom_save_path_confirmed,
        },
        name=f"paper-search-parse-job-{job_id}",
        daemon=True,
    )
    with _PARSE_JOB_LOCK:
        _PARSE_JOBS[job_id] = {
            "job_id": job_id,
            "status": "submitted",
            "selection_token": selection_token,
            "selected_indices": selected_indices,
            "save_path": save_path,
            "use_scihub": use_scihub,
            "mode": mode,
            "backend": backend,
            "force": force,
            "custom_save_path_confirmed": bool(custom_save_path_confirmed),
            "created_at": created_at,
            "created_epoch": created_epoch,
            "updated_at": created_at,
            "updated_epoch": created_epoch,
            "message": preview_message,
            "current": "Queued for MinerU parsing.",
            "items": items,
            "total": len(items),
            "selected_indices_list": parsed_indices,
            "completed_items": sum(
                1 for item in items if str(item.get("status") or "") == "skipped"
            ),
            "progress_percent": 0,
            "task": None,
            "thread": worker,
        }
        _refresh_parse_job_progress(_PARSE_JOBS[job_id])
        _persist_parse_job(job_id, _PARSE_JOBS[job_id], active=True)
    worker.start()
    return {
        "status": "submitted",
        "job_id": job_id,
        "message": f"MinerU parsing started for {len(active_items)} paper(s).",
        "total": len(items),
        "items": items,
        "progress_percent": _PARSE_JOBS[job_id].get("progress_percent", 0),
    }


# ===========================================================================
# Module exports
# ===========================================================================

__all__ = [
    "register_core_tools",
    "_run_parse_selected_papers",
    "_run_submit_parse_job",
    "_run_download_and_parse_selected_papers",
]


async def _run_download_and_parse_selected_papers(
    *,
    selection_token: str,
    selected_indices: str = "all",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
) -> Dict[str, Any]:
    """Submit the user-confirmed download-and-parse workflow as one job.

    The parse worker resolves or downloads each selected PDF before invoking
    MinerU, so no paper is downloaded until the caller has supplied the user's
    selected indices.
    """
    result = await _run_submit_parse_job(
        parse_fn=_run_parse_selected_papers,
        selection_token=selection_token,
        selected_indices=selected_indices,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )
    if isinstance(result, dict):
        result.setdefault("interaction", "download_and_parse_selected")
        result.setdefault("selection_semantics", "download_and_parse_selected_only")
        result.setdefault(
            "message",
            "Selected PDFs are being downloaded and parsed with MinerU.",
        )
    return result


# ===========================================================================
# Tool registration
# ===========================================================================


def register_core_tools(mcp):  # noqa: C901, PLR0915
    """Register session, job management, and source diagnostic tools on *mcp*."""

    # ------------------------------------------------------------------
    # parse_selected_papers
    # ------------------------------------------------------------------

    @mcp.tool()
    async def parse_selected_papers(
        selection_token: str,
        selected_indices: str = "all",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
        custom_save_path_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Parse papers from a saved search session by numbered selection.

        selected_indices accepts "all", comma-separated values such as "1,3,5",
        or ranges such as "2-4". Sci-Hub remains opt-in via use_scihub.
        """
        return await _run_parse_selected_papers(
            selection_token=selection_token,
            selected_indices=selected_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )

    # ------------------------------------------------------------------
    # submit_parse_job
    # ------------------------------------------------------------------

    @mcp.tool(meta=_WIDGET_ACCESSIBLE_TOOL_META)
    async def submit_parse_job(
        selection_token: str,
        selected_indices: str = "all",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
        custom_save_path_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Submit parse_selected_papers as a background job and return immediately."""
        return await _run_submit_parse_job(
            parse_fn=parse_selected_papers,
            selection_token=selection_token,
            selected_indices=selected_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )

    # ------------------------------------------------------------------
    # download_and_parse_selected_papers
    # ------------------------------------------------------------------

    @mcp.tool()
    async def download_and_parse_selected_papers(
        selection_token: str,
        selected_indices: str = "all",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
        custom_save_path_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Download selected PDFs after user confirmation, then parse with MinerU."""
        return await _run_download_and_parse_selected_papers(
            selection_token=selection_token,
            selected_indices=selected_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )

    # ------------------------------------------------------------------
    # get_parse_job_status
    # ------------------------------------------------------------------

    @mcp.tool(meta=_WIDGET_ACCESSIBLE_TOOL_META)
    async def get_parse_job_status(job_id: str) -> Dict[str, Any]:
        """Return current background parse job state and result when completed."""
        return _parse_job_snapshot(job_id)

    # ------------------------------------------------------------------
    # list_parse_jobs
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_parse_jobs() -> Dict[str, Any]:
        """List persisted and in-memory background parse jobs."""
        stored_by_id = {
            str(job.get("job_id")): dict(job)
            for job in await asyncio.to_thread(list_parse_job_records)
        }
        with _PARSE_JOB_LOCK:
            for job_id, job in _PARSE_JOBS.items():
                stored_by_id[job_id] = _serializable_parse_job(job, active=True)
        jobs = list(stored_by_id.values())
        jobs.sort(key=lambda job: float(job.get("created_epoch") or 0), reverse=True)
        return {"jobs": jobs, "total": len(jobs)}

    # ------------------------------------------------------------------
    # cancel_parse_job
    # ------------------------------------------------------------------

    @mcp.tool()
    async def cancel_parse_job(job_id: str) -> Dict[str, Any]:
        """Best-effort cancellation for a running background parse job."""
        with _PARSE_JOB_LOCK:
            job = _PARSE_JOBS.get(job_id)
            task = job.get("task") if isinstance(job, dict) else None
            worker = job.get("thread") if isinstance(job, dict) else None
        if not job:
            stored = read_parse_job(job_id)
            if stored:
                return {
                    "status": str(stored.get("status") or "unknown"),
                    "job_id": job_id,
                    "active": False,
                }
            return {"status": "not_found", "job_id": job_id}
        if task is not None and not task.done():
            task.cancel()
            _update_parse_job(job_id, status="cancel_requested", message="Cancellation requested.")
            return {"status": "cancel_requested", "job_id": job_id}
        if isinstance(worker, threading.Thread) and worker.is_alive():
            return {
                "status": "cancel_unavailable",
                "job_id": job_id,
                "active": True,
                "message": "Thread-backed parse jobs cannot be canceled after they start.",
            }
        snapshot = _serializable_parse_job(job, active=False)
        _persist_parse_job(job_id, job, active=False)
        return {
            "status": str(snapshot.get("status") or "completed"),
            "job_id": job_id,
            "active": False,
        }

    # ------------------------------------------------------------------
    # resume_parse_job
    # ------------------------------------------------------------------

    @mcp.tool()
    async def resume_parse_job(
        job_id: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Resume a background parse job that was interrupted (e.g., server restart).

        Re-submits only the items that have not yet completed successfully.
        Use force=True to re-parse all items including previously completed ones.
        """
        stored = await asyncio.to_thread(read_parse_job, job_id)
        if not stored:
            return {"status": "not_found", "job_id": job_id, "message": "Parse job not found."}

        items = stored.get("items") or []
        if not isinstance(items, list):
            items = []

        pending = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("status") or "").lower() not in _PARSE_ITEM_TERMINAL_STATUSES
        ]

        if not pending and not force:
            return {
                "status": "already_completed",
                "job_id": job_id,
                "message": "All items are already in a terminal state. Use force=True to re-parse.",
                "total": len(items),
            }

        if force:
            pending_indices = ",".join(
                str(item.get("index", "")) for item in items if isinstance(item, dict)
            )
        else:
            pending_indices = ",".join(
                str(item.get("index", "")) for item in pending if isinstance(item, dict)
            )

        if not pending_indices:
            return {
                "status": "error",
                "job_id": job_id,
                "message": "Could not determine item indices to resume.",
            }

        selection_token = stored.get("selection_token", "")
        if not selection_token:
            return {
                "status": "error",
                "job_id": job_id,
                "message": "Job record has no selection_token.",
            }

        save_path = stored.get("save_path") or DEFAULT_SAVE_PATH
        custom_save_path_confirmed = bool(stored.get("custom_save_path_confirmed", False))

        return await submit_parse_job(
            selection_token=selection_token,
            selected_indices=pending_indices,
            save_path=save_path,
            use_scihub=bool(stored.get("use_scihub", False)),
            mode=stored.get("mode") or "auto",
            backend=stored.get("backend") or "",
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )

    # ------------------------------------------------------------------
    # dismiss_parse_prompt
    # ------------------------------------------------------------------

    @mcp.tool(meta=_WIDGET_ACCESSIBLE_TOOL_META)
    async def dismiss_parse_prompt(
        selection_token: str,
        prompt_id: str = "",
        reason: str = "timeout",
    ) -> Dict[str, Any]:
        """Dismiss the optional post-download MinerU prompt without parsing."""
        return await asyncio.to_thread(
            dismiss_parse_prompt_state,
            selection_token,
            prompt_id=prompt_id,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # list_search_sessions
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_search_sessions() -> Dict[str, Any]:
        """List saved search-result selection sessions."""
        sessions = await asyncio.to_thread(cache_list_search_sessions)
        return {"sessions": sessions, "total": len(sessions)}

    # ------------------------------------------------------------------
    # get_search_session
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_search_session(selection_token: str) -> Dict[str, Any]:
        """Return one saved search session as numbered parse candidates."""
        session = await asyncio.to_thread(cache_get_search_session, selection_token)
        if not session:
            return {"status": "not_found", "selection_token": selection_token}

        papers = session.get("papers", [])
        if not isinstance(papers, list):
            papers = []
        return {
            "status": "ok",
            "selection_token": session.get("selection_token", selection_token),
            "query": session.get("query", ""),
            "sources": session.get("sources", ""),
            "created_at": session.get("created_at", ""),
            "metadata": session.get("metadata", {}),
            "papers": [
                _paper_parse_candidate(paper, index + 1)
                for index, paper in enumerate(papers)
                if isinstance(paper, dict)
            ],
            "total": len(papers),
        }

    # ------------------------------------------------------------------
    # export_citation
    # ------------------------------------------------------------------

    @mcp.tool()
    async def export_citation(
        paper: Dict[str, Any],
        format: str = "bibtex",
        key: str = "",
    ) -> str:
        """Export one paper dictionary as BibTeX, RIS, or EndNote."""
        return await asyncio.to_thread(format_citation, paper, format, key)

    # ------------------------------------------------------------------
    # export_citations_batch
    # ------------------------------------------------------------------

    @mcp.tool()
    async def export_citations_batch(
        selection_token: str,
        format: str = "bibtex",
        selected_indices: str = "all",
    ) -> Dict[str, Any]:
        """Export selected papers from a saved search session as citations."""
        session = await asyncio.to_thread(cache_get_search_session, selection_token)
        if not session:
            return {
                "status": "not_found",
                "selection_token": selection_token,
                "message": "Search session not found.",
            }
        papers = session.get("papers", [])
        if not isinstance(papers, list):
            papers = []
        try:
            indices = _parse_selected_indices(selected_indices, len(papers))
        except ValueError as exc:
            return {
                "status": "invalid_selection",
                "selection_token": selection_token,
                "message": str(exc),
                "total": len(papers),
            }
        selected_papers = [papers[index - 1] for index in indices if isinstance(papers[index - 1], dict)]
        try:
            citation_text = await asyncio.to_thread(format_citations, selected_papers, format)
            entries = [
                {
                    "index": index,
                    "title": str(papers[index - 1].get("title") or ""),
                    "citation": await asyncio.to_thread(format_citation, papers[index - 1], format),
                }
                for index in indices
                if isinstance(papers[index - 1], dict)
            ]
        except ValueError as exc:
            return {
                "status": "invalid_format",
                "selection_token": selection_token,
                "message": str(exc),
                "supported_formats": ["bibtex", "ris", "endnote"],
            }
        return {
            "status": "ok",
            "selection_token": selection_token,
            "query": session.get("query", ""),
            "format": format,
            "selected_indices": indices,
            "total": len(entries),
            "citation": citation_text,
            "entries": entries,
        }

    # ------------------------------------------------------------------
    # delete_search_session
    # ------------------------------------------------------------------

    @mcp.tool(meta=_WIDGET_ACCESSIBLE_TOOL_META)
    async def delete_search_session(selection_token: str) -> Dict[str, Any]:
        """Delete one saved search-result selection session."""
        deleted = await asyncio.to_thread(cache_delete_search_session, selection_token)
        return {"selection_token": selection_token, "deleted": deleted}

    # ------------------------------------------------------------------
    # list_sources
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_sources(include_capabilities: bool = True) -> Dict[str, Any]:
        """List configured academic sources and their search/download/read capabilities."""
        sources = []
        for source in ALL_SOURCES:
            entry: Dict[str, Any] = {"name": source}
            if include_capabilities:
                entry.update(SOURCE_CAPABILITIES.get(source, {}))
                entry["reliability"] = _source_reliability(source)
            sources.append(entry)
        return {
            "sources": sources,
            "sources_ranked_by_reliability": _rank_sources_by_reliability(ALL_SOURCES),
            "total": len(sources),
        }

    # ------------------------------------------------------------------
    # diagnose_paper_sources
    # ------------------------------------------------------------------

    @mcp.tool()
    async def diagnose_paper_sources(sources: str = "all") -> Dict[str, Any]:
        """Report configured API keys, source capabilities, and disabled academic sources."""
        raw_sources = [part.strip().lower() for part in (sources or "").split(",") if part.strip()]
        if len(raw_sources) == 1 and raw_sources[0] in SEARCH_PROFILES:
            requested = [
                source for source in SEARCH_PROFILES[raw_sources[0]] if source in ALL_SOURCES
            ]
        elif len(raw_sources) == 1 and raw_sources[0] in {"all", "deep"}:
            requested = list(ALL_SOURCES)
        elif raw_sources:
            requested = [source for source in raw_sources if source in ALL_SOURCES]
        else:
            requested = _parse_sources(sources)
        disabled = sorted(_disabled_sources())
        reports = [_source_capability_report(source) for source in requested]
        ranked_requested = _rank_sources_by_reliability(requested)
        return {
            "status": "ok",
            "sources_requested": sources,
            "sources_used": requested,
            "sources_ranked_by_reliability": ranked_requested,
            "disabled_sources": disabled,
            "_disabled_sources": disabled,
            "disable_env": "PAPER_SEARCH_MCP_DISABLED_SOURCES",
            "default_agent_skill_fast_sources": [
                source
                for source in AGENT_SKILL_FAST_SOURCES
                if source not in _disabled_sources()
            ],
            "agent_skill_broad_sources": [
                source
                for source in AGENT_SKILL_BROAD_SOURCES
                if source not in _disabled_sources()
            ],
            "mineru": _source_capability_report("mineru"),
            "sources": reports,
            "notes": [
                "Missing Semantic Scholar key is not fatal; it mainly lowers rate limits. Semantic downloads still require openAccessPdf metadata.",
                "Zenodo public search/download does not require a token. Tokens only improve rate limits or access to records you are allowed to read.",
                "SSRN has no public full-text API; PDF download is best-effort and often unavailable.",
                "Set PAPER_SEARCH_MCP_DISABLED_SOURCES to a comma-separated list to skip unstable or metadata-only sources.",
            ],
        }
