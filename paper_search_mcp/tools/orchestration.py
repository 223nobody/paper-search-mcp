# paper_search_mcp/tools/orchestration.py
"""
High-level orchestration MCP tools extracted from server.py.

Provides register_orchestration_tools(mcp, searchers) which registers:
- search_papers
- search_papers_for_parsing
- search_papers_with_elicitation
- crawl_papers_for_selection
- paper_research_workflow
- download_selected_papers
- resume_download
- crawl_download_parse_papers
- download_with_fallback

Plus helper functions:
- _numbered_paper_fallback
- _create_paper_selection_result

Uses lazy imports for cross-tool calls to avoid circular dependencies.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import Context
from fastmcp.tools.base import ToolResult

from ..config import env_file_path, get_env
from ..utils import DEFAULT_SAVE_PATH, resolve_save_path

# ---------------------------------------------------------------------------
# Cache imports
# ---------------------------------------------------------------------------
from ..cache import (
    create_search_session as _cache_create_search_session,
    delete_session_download_state as _cache_delete_session_download_state,
    get_search_session as _cache_get_search_session,
    read_parse_prompt_state as _cache_read_parse_prompt_state,
    read_json,
    read_session_download_state as _cache_read_session_download_state,
    update_search_session_metadata as _cache_update_search_session_metadata,
    write_parse_prompt_state as _cache_write_parse_prompt_state,
    write_session_download_state as _cache_write_session_download_state,
    _session_path as cache_session_path,
    record_download,
    sha256_file,
    utc_now,
    write_json,
)

# ---------------------------------------------------------------------------
# Engine search imports
# ---------------------------------------------------------------------------
from ..engine.search import (
    ALL_SOURCES,
    SEARCH_CACHE_TTL_ENV,
    SEARCH_PROFILE_ENV,
    SEARCH_SOURCE_TIMEOUT_ENV,
    SEARCH_TIMEOUT_ENV,
    SOURCE_CAPABILITIES,
    _source_reliability_score,
    _cached_search_result as _cached_search_result,
    _env_float,
    _env_int,
    _parse_sources as _parse_sources,
    _rank_sources_by_reliability,
    _search_cache_key as _search_cache_key,
    _search_source_with_timeout as _search_source_with_timeout,
    _source_reliability,
    _store_search_result as _store_search_result,
)

# ---------------------------------------------------------------------------
# Engine paper imports
# ---------------------------------------------------------------------------
from ..engine.paper import (
    AGENT_SKILL_RANKING_PROFILE,
    _agent_skill_profile_score,
    _classify_query_intent,
    _dedupe_papers,
    _download_route_for_candidate,
    _extract_arxiv_id,
    _paper_arxiv_id,
    _paper_field,
    _paper_parse_candidate,
    _paper_year,
    _paper_profile_text,
    _paper_unique_key,
    _paper_value,
    _rank_papers_for_profile,
    _ranking_profile_name,
    _searcher_for_source as _engine_searcher_for_source,
    _source_from_identifier,
)

# ---------------------------------------------------------------------------
# Engine download imports
# ---------------------------------------------------------------------------
from ..engine.download import (
    DOWNLOAD_CONCURRENCY_ENV,
    DOWNLOAD_TIMEOUT_ENV,
    _download_from_url,
    _download_selected_session_paper,
    _download_with_fallback_path,
    _find_existing_pdf,
    _invalid_mcp_save_path,
    _is_valid_pdf_file,
    _pdf_result_metadata,
)

# ---------------------------------------------------------------------------
# Engine parse imports (selection / UI helpers)
# ---------------------------------------------------------------------------
from ..engine.parse import (
    AGENT_SKILL_RANKING_PROFILE as _PARSE_AGENT_SKILL_RANKING_PROFILE,  # noqa: F811
    AUTO_PARSE_SAVED_PDF_LIMIT,
    PAPER_SELECTION_WIDGET_URI,
    PAPER_SELECTION_WIDGET_TOOL,
    SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
    SELECTION_SEMANTICS_DOWNLOAD_ONLY,
    SELECTION_SEMANTICS_PARSE,
    _build_paper_selection_schema,
    _parse_prompt_timeout_metadata,
    _terminal_parse_prompt_for_download,
    _write_pending_parse_prompt_state,
    _elicitation_option_label,
    _paper_from_download_metadata,
    _paper_selection_app_prompt,
    _codex_app_display_candidates,
    _codex_recommended_selected_indices,
    _reindexed_display_candidates,
    _parse_elicitation_selected_indices,
    _parse_selected_indices,
    _promote_paper_selection_app,
    _selection_surface_policy,
    _selection_semantics_name,
    _should_promote_paper_selection_app,
    _strip_widget_meta,
    _format_selected_indices,
    _workflow_parse_execution_name,
    dismiss_parse_prompt_state as _dismiss_parse_prompt_state,
)

# ---------------------------------------------------------------------------
# Module-level env helpers (not in engine modules)
# ---------------------------------------------------------------------------
ALLOW_CUSTOM_SAVE_PATH_ENV = "ALLOW_CUSTOM_SAVE_PATH"
REQUIRE_EXPLICIT_SAVE_PATH_ENV = "REQUIRE_EXPLICIT_SAVE_PATH"
LARGE_BATCH_SELECTION_ENV = "LARGE_BATCH_SELECTION"
PARSE_PROMPT_TIMEOUT_SECONDS_ENV = "PARSE_PROMPT_TIMEOUT_SECONDS"
PARSE_PROMPT_TIMEOUT_ACTION_ENV = "PARSE_PROMPT_TIMEOUT_ACTION"
PARSE_PROMPT_ALLOW_REOPEN_ENV = "PARSE_PROMPT_ALLOW_REOPEN"
LOCAL_PAPER_SELECTION_TOOL = "open_paper_selection_page"

PARSE_PROMPT_STATE_PENDING = "pending"
PARSE_PROMPT_STATE_COMPLETED_NO_PARSE = "completed_no_parse"
PARSE_PROMPT_STATE_TIMED_OUT_NO_PARSE = "timed_out_no_parse"
TERMINAL_PARSE_PROMPT_STATES = {
    PARSE_PROMPT_STATE_COMPLETED_NO_PARSE,
    PARSE_PROMPT_STATE_TIMED_OUT_NO_PARSE,
}

PARSE_PROMPT_TIMEOUT_MESSAGE = (
    "PDFs were saved. MinerU parsing was not started because there was no "
    "action for {timeout_seconds} seconds. To parse later, run an explicit "
    "parse command for the saved PDFs."
)

logger = logging.getLogger(__name__)


def _prefer_local_selection_surface(result: Dict[str, Any]) -> Dict[str, Any]:
    """Expose localhost selection details when it is the active fallback."""
    if not isinstance(result, dict):
        return result
    prompt = result.get("parse_prompt")
    local = result.get("local_browser")
    if not isinstance(local, dict):
        if isinstance(prompt, dict) and isinstance(prompt.get("local_browser"), dict):
            local = prompt["local_browser"]
            result["local_browser"] = local
    if not isinstance(local, dict) or local.get("status") != "ok":
        return result

    surface = local.get("selection_surface")
    surface_name = surface.get("surface") if isinstance(surface, dict) else ""

    if surface_name == "hybrid":
        result.setdefault("interaction", "mcp_app")
        result["local_browser_url"] = local.get("url", "")
        result["page_id"] = local.get("page_id", "")
        result["opened"] = bool(local.get("opened", False))
        result.setdefault("recommended_tool", PAPER_SELECTION_WIDGET_TOOL)
        result.setdefault("selection_token", local.get("selection_token", ""))
        result["recommended_url"] = local.get("url", "")
        if isinstance(surface, dict):
            result["selection_surface"] = surface
        return result

    result["interaction"] = local.get("interaction", "local_browser_checkbox")
    result["recommended_tool"] = LOCAL_PAPER_SELECTION_TOOL
    result["recommended_url"] = local.get("url", "")
    result["local_browser_url"] = local.get("url", "")
    result["page_id"] = local.get("page_id", "")
    result["opened"] = bool(local.get("opened", False))
    if "selection_timeout_seconds" in local:
        result["selection_timeout_seconds"] = int(
            local.get("selection_timeout_seconds") or 0
        )
    if "selection_expires_at" in local:
        result["selection_expires_at"] = str(local.get("selection_expires_at") or "")
    if isinstance(local.get("selection_surface"), dict):
        result["selection_surface"] = local["selection_surface"]
    if isinstance(prompt, dict):
        prompt["recommended_tool"] = LOCAL_PAPER_SELECTION_TOOL
        prompt["recommended_url"] = local.get("url", "")
        prompt["local_browser_url"] = local.get("url", "")
        prompt["interaction"] = local.get("interaction", "local_browser_checkbox")
        if "selection_timeout_seconds" in local:
            prompt["selection_timeout_seconds"] = int(
                local.get("selection_timeout_seconds") or 0
            )
        if "selection_expires_at" in local:
            prompt["selection_expires_at"] = str(local.get("selection_expires_at") or "")
    result.setdefault("selection_token", local.get("selection_token", ""))
    result["message"] = (
        local.get("message")
        or result.get("message")
        or "Use the opened localhost checkbox page to select and download papers."
    )
    return result


def _to_widget_tool_result(result: Dict[str, Any]) -> Any:
    """Return the result dict unchanged, preserving ``_meta`` as a regular key.

    ``_promote_paper_selection_app`` sets ``result["_meta"]`` when the
    selection surface is ``mcp_app``.  Previously this function wrapped the
    dict in a FastMCP ``ToolResult`` so the framework's ``to_mcp_result()``
    would promote ``meta`` to ``CallToolResult._meta`` on the wire.

    However, FastMCP's auto-generated Pydantic output model (derived from
    the ``-> Dict[str, Any]`` return annotation) rejects ``ToolResult``
    instances because the model's ``result`` field expects a plain dict.
    Returning the plain dict avoids the Pydantic validation error while
    keeping the ``_meta`` data available in the JSON output for clients
    that inspect ``result._meta``.

    When ``_meta`` is absent the dict is returned unchanged.
    """
    if not isinstance(result, dict):
        return result
    # Keep _meta in the dict — FastMCP's Pydantic model expects a plain dict,
    # not a ToolResult.  MCP Apps hosts can read _meta from the JSON payload.
    return result


def _unwrap_tool_result(value: Any) -> tuple:
    """Unwrap a FastMCP ToolResult to (raw_dict, meta_dict_or_none).

    When one MCP tool calls another internally, the callee may return a
    ``ToolResult`` carrying widget metadata.  This helper extracts the
    raw business dict and any ``_meta`` so the caller can read
    ``.get("papers")``, ``.get("selection_token")``, etc.
    """
    if isinstance(value, ToolResult):
        meta = dict(value.meta) if getattr(value, "meta", None) else None
        structured = getattr(value, "structured_content", None)
        if isinstance(structured, dict) and isinstance(
            structured.get("result"), dict
        ):
            return structured["result"], meta
        elif isinstance(structured, dict):
            return structured, meta
        else:
            return {}, meta
    return value, None


def _split_env_csv(value: str) -> List[str]:
    """Split a comma-separated environment variable value into trimmed parts."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_flag_enabled(name: str, default: str = "false") -> bool:
    value = get_env(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _custom_save_paths_allowed() -> bool:
    return _env_flag_enabled(ALLOW_CUSTOM_SAVE_PATH_ENV, default="true")


def _explicit_save_path_required() -> bool:
    return _env_flag_enabled(REQUIRE_EXPLICIT_SAVE_PATH_ENV, default="true")


# ---------------------------------------------------------------------------
# Save-path policy (local copies used by orchestration tools)
# ---------------------------------------------------------------------------
def _mcp_save_path_metadata(
    save_path: str, *, custom_save_path_confirmed: bool = False
) -> Dict[str, Any]:
    resolved = resolve_save_path(save_path)
    default = resolve_save_path(DEFAULT_SAVE_PATH)
    return {
        "save_path": resolved,
        "default_save_path": default,
        "save_path_defaulted": resolved == default,
        "custom_save_path_confirmed": bool(custom_save_path_confirmed),
    }


# ---------------------------------------------------------------------------
# Large-batch selection helpers (complex enough to keep here)
# ---------------------------------------------------------------------------
def _large_batch_selection_policy_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    if normalized in {
        "never", "none", "off", "false", "no", "disable", "disabled", "bypass",
    }:
        return "never"
    if normalized in {
        "always", "prompt", "manual", "select", "selection", "checkbox", "ask",
    }:
        return "always"
    return "auto"


def _large_batch_selection_satisfied(session: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(session, dict):
        return False
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("large_batch_selection_satisfied"))


def _confirmed_large_batch_indices(session: Optional[Dict[str, Any]]) -> str:
    if not isinstance(session, dict):
        return ""
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    if not metadata.get("large_batch_selection_satisfied"):
        return ""
    return str(metadata.get("confirmed_selected_indices") or "").strip()


def _selected_indices_was_explicit(value: Any) -> bool:
    """Return True only when a caller supplied a concrete selection."""
    if isinstance(value, str):
        return bool(value.strip())
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return True


def _large_batch_confirmation_mismatch(
    session: Optional[Dict[str, Any]],
    indices: List[int],
) -> bool:
    confirmed = _confirmed_large_batch_indices(session)
    if not confirmed:
        return False
    try:
        papers = session.get("papers", []) if isinstance(session, dict) else []
        total = len(papers) if isinstance(papers, list) else max(indices or [0])
        confirmed_indices = _parse_selected_indices(confirmed, total)
    except Exception:
        return True
    return list(indices) != list(confirmed_indices)


def _should_require_large_batch_selection(
    item_count: int,
    *,
    large_batch_selection: str = "auto",
    bypass_large_batch_selection: bool = False,
    session: Optional[Dict[str, Any]] = None,
    public_call: bool = False,
    explicit_user_selection: bool = False,
) -> bool:
    if _large_batch_selection_satisfied(session):
        return False
    if not public_call and bypass_large_batch_selection:
        return False
    policy = _large_batch_selection_policy_name(
        large_batch_selection or get_env(LARGE_BATCH_SELECTION_ENV, "auto")
    )
    if policy == "never" and not public_call:
        return False
    if policy == "always":
        return item_count > 0
    # ── Cross-batch gate: block when the ORIGINAL session requested_count
    #     was above the limit even if this single call only picks ≤ 10 papers.
    #     Skip this gate when the user has explicitly chosen which papers to
    #     download (e.g. via numbered fallback indices "1,3,5").
    if not explicit_user_selection:
        if isinstance(session, dict):
            metadata = session.get("metadata")
            if isinstance(metadata, dict):
                try:
                    session_requested = max(0, int(metadata.get("requested_count") or 0))
                except (TypeError, ValueError):
                    session_requested = 0
                if session_requested > AUTO_PARSE_SAVED_PDF_LIMIT:
                    return True
    return item_count > AUTO_PARSE_SAVED_PDF_LIMIT


# ---------------------------------------------------------------------------
# Multi-round search helpers (progressive retry for crawl_papers_for_selection)
# ---------------------------------------------------------------------------
def _merge_search_results(
    base: Dict[str, Any],
    additional: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge two ``search_papers`` result dicts across retry rounds.

    Combines ``source_results`` (taking the max per source), ``sources_used``
    (deduplicated, order preserved), ``errors`` (later wins for same key), and
    ``papers`` (concatenated; cross-round dedup is handled upstream).
    """
    merged = dict(base)
    merged["papers"] = _dedupe_papers(
        base.get("papers", []) + additional.get("papers", []),
        query="",
    )

    # Merge source_results — take the higher count per source
    base_sr = dict(base.get("source_results", {}))
    add_sr = additional.get("source_results", {})
    for source, count in add_sr.items():
        base_sr[source] = max(base_sr.get(source, 0), count)
    merged["source_results"] = base_sr

    # Merge sources_used — dedup preserving order
    base_used = list(base.get("sources_used", []))
    add_used = additional.get("sources_used", [])
    seen = set(base_used)
    for s in add_used:
        if s not in seen:
            base_used.append(s)
            seen.add(s)
    merged["sources_used"] = base_used

    # Merge errors — later value wins for same key
    base_errs = dict(base.get("errors", {}))
    add_errs = additional.get("errors", {})
    base_errs.update(add_errs)
    merged["errors"] = base_errs

    return merged


def _count_download_ready_papers(papers: List[Dict[str, Any]]) -> int:
    """Lightweight download-ready count without building full parse candidates."""
    count = 0
    for paper in papers:
        arxiv_id = _paper_arxiv_id(paper)
        ready, _, _ = _download_route_for_candidate(
            source=paper.get("source", ""),
            paper_id=paper.get("paper_id", ""),
            doi=paper.get("doi", ""),
            title=paper.get("title", ""),
            pdf_url=paper.get("pdf_url", ""),
            local_pdf_path=paper.get("local_pdf_path", ""),
            arxiv_id=arxiv_id,
        )
        if ready is not False:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Workflow intent helpers
# ---------------------------------------------------------------------------
def _workflow_intent_name(intent: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (intent or "").strip().lower()).strip("_")
    return normalized or "search_download_parse"


def _intent_explicitly_requests_parse(intent_name: str) -> bool:
    return intent_name in {
        "parse",
        "download_parse",
        "download_and_parse",
        "search_download_parse_now",
        "search_download_and_parse",
        "crawl_download_parse",
        "mineru",
        "mineru_parse",
        "parse_with_mineru",
    }


def _workflow_selection_indices(
    selected_indices: str,
    selection_mode: str,
    count: int,
    total: int,
    papers: Optional[List[Dict[str, Any]]] = None,
) -> str:
    explicit = (selected_indices or "").strip()
    if explicit:
        return explicit

    mode = (
        re.sub(r"[^a-z0-9]+", "_", (selection_mode or "").strip().lower()).strip("_")
        or "auto_top"
    )
    if mode in {"all", "auto_all"}:
        return "all"

    try:
        limit = max(1, int(count or 1))
    except (TypeError, ValueError):
        limit = 1
    limit = min(limit, max(0, total))
    if isinstance(papers, list) and papers:
        recommended = _codex_recommended_selected_indices(
            papers,
            requested_count=limit,
            fallback_total=total,
        )
        if recommended:
            return recommended
    return ",".join(str(index) for index in range(1, limit + 1))


# ---------------------------------------------------------------------------
# _numbered_paper_fallback
# ---------------------------------------------------------------------------
def _numbered_paper_fallback(candidates: List[Dict[str, Any]]) -> List[str]:
    """Return numbered paper strings for terminal fallback display."""
    return [_elicitation_option_label(candidate) for candidate in candidates]


def _requested_selection_indices(requested_count: int, total: int) -> str:
    if requested_count <= 0 or total <= 0:
        return ""
    end = min(int(requested_count), int(total))
    if end <= 0:
        return ""
    return f"1-{end}" if end > 1 else "1"


# ---------------------------------------------------------------------------
# _create_paper_selection_result
# ---------------------------------------------------------------------------
async def _create_paper_selection_result(
    *,
    query: str,
    max_results_per_source: int,
    sources: str,
    year: Optional[str],
    search_result: Dict[str, Any],
    interaction: str,
    ranking_profile: str = "",
    action_tool: str = "parse_selected_papers",
    action_verb: str = "parse",
    selection_semantics: str = SELECTION_SEMANTICS_PARSE,
    requested_count: int = 0,
) -> Dict[str, Any]:
    """Create a paper selection session from search results."""
    semantics = _selection_semantics_name(selection_semantics)
    papers = search_result.get("papers", [])
    if not isinstance(papers, list):
        papers = []

    ranked_papers = _rank_papers_for_profile(
        papers,
        ranking_profile=ranking_profile,
        query=query,
    )
    profile = _ranking_profile_name(ranking_profile)
    requested_count = max(0, int(requested_count or 0))
    parse_execution = (
        "none"
        if semantics == SELECTION_SEMANTICS_DOWNLOAD_ONLY
        else "background"
    )
    full_session = await asyncio.to_thread(
        _cache_create_search_session,
        query,
        sources,
        ranked_papers,
        {
            "year": year or "",
            "max_results_per_source": max_results_per_source,
            "sources_used": search_result.get("sources_used", []),
            "source_results": search_result.get("source_results", {}),
            "errors": search_result.get("errors", {}),
            "interaction": interaction,
            "ranking_profile": profile,
            "action_tool": action_tool,
            "selection_semantics": semantics,
            "requested_count": requested_count,
            "parse_execution": parse_execution,
            "selection_session_role": "full_ranked_results",
        },
    )
    full_candidates = [
        _paper_parse_candidate(paper, index + 1)
        for index, paper in enumerate(ranked_papers)
    ]
    full_parse_ready_total = sum(
        1 for candidate in full_candidates if candidate["parse_ready"]
    )
    if requested_count > 0:
        display_source_candidates = _codex_app_display_candidates(
            full_candidates,
            requested_count=requested_count,
        )
    else:
        display_source_candidates = full_candidates

    source_indices: List[int] = []
    shortlist_papers: List[Dict[str, Any]] = []
    for candidate in display_source_candidates:
        try:
            source_index = int(candidate.get("index") or 0)
        except (TypeError, ValueError):
            source_index = 0
        if source_index <= 0 or source_index > len(ranked_papers):
            continue
        paper = dict(ranked_papers[source_index - 1])
        paper.setdefault("source_index", source_index)
        paper.setdefault("original_index", source_index)
        shortlist_papers.append(paper)
        source_indices.append(source_index)

    if not shortlist_papers and ranked_papers:
        shortlist_limit = requested_count or len(ranked_papers)
        for source_index, paper in enumerate(ranked_papers[:shortlist_limit], start=1):
            item = dict(paper)
            item.setdefault("source_index", source_index)
            item.setdefault("original_index", source_index)
            shortlist_papers.append(item)
            source_indices.append(source_index)

    if requested_count > 0:
        session = await asyncio.to_thread(
            _cache_create_search_session,
            query,
            sources,
            shortlist_papers,
            {
                "year": year or "",
                "max_results_per_source": max_results_per_source,
                "sources_used": search_result.get("sources_used", []),
                "source_results": search_result.get("source_results", {}),
                "errors": search_result.get("errors", {}),
                "interaction": interaction,
                "ranking_profile": profile,
                "action_tool": action_tool,
                "selection_semantics": semantics,
                "requested_count": requested_count,
                "parse_execution": parse_execution,
                "selection_session_role": "display_shortlist",
                "full_selection_token": full_session["selection_token"],
                "source_selection_token": full_session["selection_token"],
                "source_indices": source_indices,
                "full_total": len(full_candidates),
                "full_parse_ready_total": full_parse_ready_total,
            },
        )
        try:
            full_metadata = full_session.get("metadata", {})
            if isinstance(full_metadata, dict):
                full_metadata.update(
                    {
                        "display_selection_token": session["selection_token"],
                        "display_source_indices": source_indices,
                        "full_total": len(full_candidates),
                        "full_parse_ready_total": full_parse_ready_total,
                    }
                )
                full_session["metadata"] = full_metadata
                full_session["updated_at"] = utc_now()
                write_json(cache_session_path(full_session["selection_token"]), full_session)
        except Exception:
            logger.debug("Failed to annotate full selection session", exc_info=True)
    else:
        session = full_session

    candidates = [
        _paper_parse_candidate(paper, index + 1)
        for index, paper in enumerate(shortlist_papers)
    ]
    candidates = _reindexed_display_candidates(candidates)
    parse_ready_total = sum(
        1 for candidate in candidates if candidate["parse_ready"]
    )
    numbered_fallback = _numbered_paper_fallback(candidates)
    action_description = (
        f"call {action_tool}(selection_token=<token>, "
        "selected_indices='1,3,5') or selected_indices='all'."
    )
    app_candidates = _codex_app_display_candidates(
        candidates,
        requested_count=requested_count,
    )
    app_candidates = _reindexed_display_candidates(app_candidates)
    recommended_indices = _codex_recommended_selected_indices(
        candidates,
        requested_count=requested_count,
        fallback_total=len(candidates),
    )
    requested_over_limit = requested_count > AUTO_PARSE_SAVED_PDF_LIMIT

    surface = _selection_surface_policy(force_open=True)
    result: Dict[str, Any] = {
        "status": "selection_required" if requested_over_limit else "ok",
        "selection_token": session["selection_token"],
        "full_selection_token": full_session["selection_token"],
        "source_selection_token": full_session["selection_token"],
        "query": query,
        "sources_requested": sources,
        "sources_used": search_result.get("sources_used", []),
        "source_results": search_result.get("source_results", {}),
        "errors": search_result.get("errors", {}),
        "ranking_profile": profile,
        "selection_semantics": semantics,
        "requested_count": requested_count,
        "instructions": (
            f"Present the numbered papers to the user. To {action_verb} "
            f"selected papers, {action_description}"
        ),
        "papers": candidates,
        "numbered_fallback": numbered_fallback,
        "fallback": {
            "interaction": "backend_session_numbered_selection",
            "selection_token": session["selection_token"],
            "full_selection_token": full_session["selection_token"],
            "instructions": (
                "If checkbox UI is unavailable, show numbered_fallback and "
                f"pass the user's numbers to {action_tool}."
            ),
            "papers": numbered_fallback,
        },
        "total": len(candidates),
        "display_total": len(candidates),
        "full_total": len(full_candidates),
        "parse_ready_total": parse_ready_total,
        "full_parse_ready_total": full_parse_ready_total,
        "raw_total": search_result.get("raw_total", len(candidates)),
        "source_indices": source_indices,
        "full_papers": full_candidates,
        "recommended_tool": PAPER_SELECTION_WIDGET_TOOL,
        "recommended_selected_indices": recommended_indices,
        # ── Multi-platform adaptation fields ──
        "detected_host": surface.get("detected_host", "unknown"),
        "app_widget_supported": surface.get("app_widget_supported", False),
        "selection_surface": surface,
    }
    # ── TUI hint for CLI-only hosts ─────────────────────────
    from ..ui.tui import is_tui_available as _is_tui_available
    from ..utils import host_is_claude_code as _host_is_claude_code
    if _is_tui_available():
        result["tui_available"] = True
        result["tui_instructions"] = (
            "A terminal-based interactive paper selector is available. "
            "Call select_papers_tui(selection_token, download_only=True|False) "
            "to let the user pick papers in the terminal."
        )
        if _host_is_claude_code() and not surface.get("app_widget_supported"):
            result["recommended_tool"] = "select_papers_tui"
    if requested_over_limit:
        result["parse_decision_required"] = True
        result["requires_user_parse_decision"] = True
        result["message"] = (
            f"{requested_count} papers were requested, above the batch "
            f"selection limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. Select papers "
            "in the checkbox UI before download."
        )
    result["app"] = _paper_selection_app_prompt(
        selection_token=session["selection_token"],
        papers=app_candidates,
        selection_semantics=semantics,
        parse_execution=parse_execution,
        requested_count=requested_count,
        full_total=len(full_candidates),
    )
    if requested_over_limit:
        await _attach_local_selection_ui(
            result,
            selection_token=session["selection_token"],
            papers=candidates,
            save_path=DEFAULT_SAVE_PATH,
            use_scihub=False,
            mode="auto",
            backend="",
            force=False,
            custom_save_path_confirmed=False,
            force_open=True,
            selection_semantics=semantics,
            parse_execution=parse_execution,
        )
    # _meta is set on the dict by _promote_paper_selection_app.
    # Callers that need a FastMCP ToolResult (MCP tool endpoints) must
    # wrap the returned dict with _to_widget_tool_result themselves.
    return _promote_paper_selection_app(_prefer_local_selection_surface(result))


# ---------------------------------------------------------------------------
# Wrappers around engine.download helpers that inject the searchers dict
# ---------------------------------------------------------------------------

async def _download_with_fallback_path_wrapper(
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    scihub_base_url: str = "https://sci-hub.se",
    download_strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
    client: Optional[httpx.AsyncClient] = None,
    *,
    _searchers: Optional[Dict[str, Any]] = None,
) -> Any:
    """Inject searchers dict into the engine download fallback chain.

    In server.py this function uses module-level searcher instances.  The
    engine version accepts ``searchers``, ``repository_searchers``, and
    ``unpaywall_resolver`` as optional keyword arguments.  This wrapper
    builds those from the registration-time ``_searchers`` dict.
    """
    searchers_dict = _searchers or {}
    repository_searchers = [
        (name, searchers_dict[name])
        for name in ("openaire", "core", "europepmc", "pmc")
        if searchers_dict.get(name) is not None
    ]
    _unpaywall = searchers_dict.get("unpaywall")
    unpaywall_resolver = _unpaywall.resolver if _unpaywall else None
    return await _download_with_fallback_path(
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
        use_scihub=use_scihub,
        scihub_base_url=scihub_base_url,
        download_strategy=download_strategy,
        use_libgen=use_libgen,
        libgen_base_url=libgen_base_url,
        searchers=searchers_dict,
        repository_searchers=repository_searchers or None,
        unpaywall_resolver=unpaywall_resolver,
        client=client,
    )


async def _download_selected_session_paper_wrapper(
    *,
    paper: Dict[str, Any],
    index: int,
    save_path: str,
    use_scihub: bool,
    client: Optional[httpx.AsyncClient] = None,
    download_strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
    _searchers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Inject searchers dict into the engine download-selected-session-paper helper."""
    searchers = _searchers or {}
    repository_searchers = [
        (name, searchers[name])
        for name in ("openaire", "core", "europepmc", "pmc")
        if searchers.get(name) is not None
    ]
    _unpaywall = searchers.get("unpaywall")
    unpaywall_resolver = _unpaywall.resolver if _unpaywall else None
    return await _download_selected_session_paper(
        paper=paper,
        index=index,
        save_path=save_path,
        use_scihub=use_scihub,
        searchers=searchers,
        repository_searchers=repository_searchers or None,
        unpaywall_resolver=unpaywall_resolver,
        client=client,
        download_strategy=download_strategy,
        use_libgen=use_libgen,
        libgen_base_url=libgen_base_url,
    )


# ---------------------------------------------------------------------------
# _download_manifest_path  (local helper used by download_selected_papers)
# ---------------------------------------------------------------------------
def _download_manifest_path(save_path: str, selection_token: str) -> str:
    from ..engine.paper import _safe_filename as _safe_fn

    root = Path(resolve_save_path(save_path))
    root.mkdir(parents=True, exist_ok=True)
    token = _safe_fn(selection_token, default="selection")
    return str(
        (root / f"paper_search_download_manifest_{token}.json").resolve()
    )


# ===========================================================================
# Helper: _attach_local_selection_ui (lazy-imports open_paper_selection_page)
# ===========================================================================

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
    """Open a local-hosted checkbox UI as a fallback for non-App hosts.

    Uses the local browser selection server from ``..ui.server``.
    """
    from ..utils import open_url_in_host as _open_url_in_host
    from ..engine.parse import _selection_semantics_name as _ssn
    from ..engine.parse import _workflow_parse_execution_name as _wpen
    from ..ui.server import _create_local_selection_page

    surface = _selection_surface_policy(force_open=force_open)
    prompt["selection_surface"] = surface
    if surface.get("surface") == "numbered_fallback":
        return prompt
    if surface.get("surface") == "mcp_app_then_local":
        prompt["fallback_tool"] = LOCAL_PAPER_SELECTION_TOOL
        prompt["status_tool"] = "get_paper_selection_surface_status"
        prompt["fallback_after_seconds"] = int(
            surface.get("fallback_after_seconds") or 0
        )
        return prompt
    # ── Hybrid mode: for tentative MCP Apps hosts (Claude Code Desktop/VSCode),
    #     create the local_browser fallback even though _meta is also set.
    if surface.get("surface") == "mcp_app":
        from ..utils import host_mcp_apps_confirmed  # noqa: PLC0415
        if host_mcp_apps_confirmed():
            return prompt
    # 已有本地浏览器页面，不重复创建
    if prompt.get("local_browser", {}).get("url"):
        return prompt

    requested_count = int(prompt.get("requested_count") or 0)
    full_total = int(prompt.get("full_total") or prompt.get("total") or len(papers))
    display_papers = _codex_app_display_candidates(
        papers,
        requested_count=requested_count,
    )
    try:
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
        )
        # ── Host-aware URL opening: VS Code stays in-editor ──
        opened = await asyncio.to_thread(_open_url_in_host, page["url"])

        prompt["local_browser"] = {
            "status": "ok",
            "interaction": "local_browser_checkbox",
            "selection_token": selection_token,
            "url": page["url"],
            "page_id": page["page_id"],
            "opened": opened,
            "selection_timeout_seconds": int(page.get("selection_timeout_seconds") or 0),
            "selection_expires_at": str(page.get("selection_expires_at") or ""),
            "selection_surface": surface,
            "papers": display_papers,
            "total": len(display_papers),
            "display_total": len(display_papers),
            "full_total": full_total,
            "requested_count": requested_count,
            "parse_ready_total": sum(
                1 for paper in display_papers if paper.get("parse_ready") is not False
            ),
            "selection_semantics": _ssn(selection_semantics),
            "parse_execution": _wpen(parse_execution),
            "message": (
                "Open the URL to select papers with checkboxes and download them from the browser page."
                if _ssn(selection_semantics) == SELECTION_SEMANTICS_DOWNLOAD_ONLY
                else "Open the URL to select papers with checkboxes and parse them from the browser page."
            ),
        }
    except Exception as exc:
        logger.exception("Failed to open local paper selection UI")
        prompt["local_browser"] = {
            "status": "error",
            "message": str(exc),
        }
    return prompt


# ===========================================================================
# _parse_prompt_for_download_results  (complex async helper)
# ===========================================================================

async def _parse_prompt_for_download_results(
    *,
    selection_token: str,
    session: Dict[str, Any],
    results: List[Dict[str, Any]],
    save_path: str,
    use_scihub: bool,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    parse_execution: str = "background",
    ctx: Optional[Context] = None,
    custom_save_path_confirmed: bool = False,
) -> Dict[str, Any]:
    """Build a parse prompt after a batch download completes.

    This mirrors the server.py version.  Lazy-imports ``parse_selected_papers``
    and ``submit_parse_job`` from ``.core`` to avoid circular imports.
    """
    # ── lazy-import cross-tool calls ──────────────────────────────────────
    try:
        from .core import _run_parse_selected_papers as _parse_selected_papers_fn  # noqa: PLC0415
    except Exception:
        return {
            "status": "error",
            "message": "parse_selected_papers is not available.",
        }

    try:
        from .core import _run_submit_parse_job as _submit_parse_job_fn  # noqa: PLC0415
    except Exception:
        _submit_parse_job_fn = None

    terminal_prompt = await asyncio.to_thread(
        _terminal_parse_prompt_for_download, selection_token
    )
    if terminal_prompt:
        return terminal_prompt

    papers: List[Dict[str, Any]] = []
    source_papers = session.get("papers", [])
    if not isinstance(source_papers, list):
        source_papers = []

    for result in results:
        if result.get("status") not in {"downloaded", "skipped_existing"}:
            continue
        pdf_path = str(result.get("pdf_path") or "").strip()
        if not pdf_path:
            continue
        index = int(result.get("index") or 0)
        original = (
            source_papers[index - 1]
            if 1 <= index <= len(source_papers)
            and isinstance(source_papers[index - 1], dict)
            else {}
        )
        candidate = (
            result.get("candidate")
            if isinstance(result.get("candidate"), dict)
            else _paper_parse_candidate(original, index)
        )
        papers.append(
            {
                "title": candidate.get("title") or Path(pdf_path).stem,
                "authors": candidate.get("authors", ""),
                "year": candidate.get("year", ""),
                "published_date": candidate.get("published_date", ""),
                "publication_venue": candidate.get(
                    "publication_venue", ""
                ),
                "source": candidate.get("source", ""),
                "paper_id": candidate.get("paper_id", ""),
                "doi": candidate.get("doi", ""),
                "pdf_url": "",
                "local_pdf_path": pdf_path,
                "url": candidate.get("url", ""),
                "original_url": candidate.get("original_url")
                or candidate.get("url", ""),
            }
        )

    parse_session = await asyncio.to_thread(
        _cache_create_search_session,
        session.get("query", ""),
        session.get("sources", ""),
        papers,
        {
            "interaction": "download_selected_papers_parse_prompt",
            "trigger": "batch_download_completed",
            "download_selection_token": selection_token,
            "save_path": resolve_save_path(save_path),
        },
    )
    candidates = [
        _paper_parse_candidate(paper, index + 1)
        for index, paper in enumerate(papers)
    ]
    selectable = [
        candidate for candidate in candidates if candidate.get("parse_ready")
    ]
    parse_execution_name = _workflow_parse_execution_name(parse_execution)
    selected_indices = [int(candidate["index"]) for candidate in selectable]
    selected_indices_arg = ",".join(
        str(index) for index in selected_indices
    )
    timeout_meta = _parse_prompt_timeout_metadata(
        download_selection_token=selection_token,
        parse_selection_token=parse_session["selection_token"],
        num_papers=len(candidates),
    )
    fallback: Dict[str, Any] = {
        "status": "ok" if selectable else "no_parse_ready_pdfs",
        "interaction": "backend_session_numbered_selection",
        "selection_token": parse_session["selection_token"],
        "download_selection_token": selection_token,
        **timeout_meta,
        "instructions": (
            "PDFs were downloaded. Ask the user whether to run MinerU parsing. "
            "If accepted, call submit_parse_job with selected_indices='all' "
            "to parse all successfully downloaded PDFs by default, or render "
            "the checkbox UI for a custom subset."
        ),
        "papers": candidates,
        "total": len(candidates),
        "parse_ready_total": len(selectable),
        "save_path": resolve_save_path(save_path),
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "parse_execution": parse_execution_name,
        "recommended_tool": (
            "submit_parse_job"
            if len(candidates) <= AUTO_PARSE_SAVED_PDF_LIMIT
            else PAPER_SELECTION_WIDGET_TOOL
        ),
        "recommended_selected_indices": "all" if selectable else "",
        "default_parse_selected_indices": "all" if selectable else "",
        "parse_decision_required": bool(selectable),
        "requires_user_parse_decision": bool(selectable),
    }
    fallback["app"] = _paper_selection_app_prompt(
        selection_token=parse_session["selection_token"],
        papers=candidates,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        download_selection_token=selection_token,
        prompt_id=str(timeout_meta.get("prompt_id") or ""),
        timeout_seconds=int(timeout_meta.get("timeout_seconds") or 0),
        expires_at=str(timeout_meta.get("expires_at") or ""),
        timeout_message=str(timeout_meta.get("timeout_message") or ""),
        allow_reopen=bool(timeout_meta.get("allow_reopen", False)),
    )

    if not selectable:
        return fallback

    if parse_execution_name == "none":
        fallback["message"] = (
            f"Saved {len(candidates)} PDFs. MinerU parsing was not started. "
            "To parse later, call submit_parse_job with the parse selection token."
        )
        fallback["recommended_tool"] = "submit_parse_job"
        fallback["recommended_selected_indices"] = "all"
        fallback["default_parse_selected_indices"] = "all"
        fallback["parse_decision_required"] = False
        fallback["requires_user_parse_decision"] = False
        return fallback

    from ..engine.parse import (
        _recent_saved_pdf_papers,
        _saved_pdf_batch_prompt_enabled,
        _saved_pdf_batch_window_seconds,
    )

    if (
        len(candidates) <= AUTO_PARSE_SAVED_PDF_LIMIT
        and _saved_pdf_batch_prompt_enabled()
    ):
        recent_papers = _recent_saved_pdf_papers(
            save_path,
            window_seconds=_saved_pdf_batch_window_seconds(),
        )
        if len(recent_papers) > AUTO_PARSE_SAVED_PDF_LIMIT:
            from ..engine.parse import _prompt_parse_saved_pdfs

            return await _prompt_parse_saved_pdfs(
                papers=recent_papers,
                query=f"recent saved PDFs in {resolve_save_path(save_path)}",
                sources="local",
                save_path=save_path,
                ctx=ctx,
                mode=mode,
                backend=backend,
                force=force,
                parse_execution="prompt",
                custom_save_path_confirmed=custom_save_path_confirmed,
                _parse_selected_papers_fn=_parse_selected_papers_fn,
                _submit_parse_job_fn=_submit_parse_job_fn,
                _attach_local_selection_ui_fn=_attach_local_selection_ui,
            )

    if parse_execution_name == "prompt":
        fallback["message"] = (
            f"Saved {len(candidates)} PDFs. Select PDFs in the checkbox UI "
            "or use numbered indices before MinerU parsing."
        )
        fallback["parse_decision_required"] = True
        fallback["requires_user_parse_decision"] = True
        fallback["recommended_tool"] = PAPER_SELECTION_WIDGET_TOOL
        fallback["recommended_selected_indices"] = ""
        if len(candidates) > AUTO_PARSE_SAVED_PDF_LIMIT:
            await _attach_local_selection_ui(
                fallback,
                selection_token=parse_session["selection_token"],
                papers=candidates,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
                # force_open=True is gated by _selection_ui_should_open(),
                # which suppresses the browser for MCP-widget-capable hosts.
                force_open=True,
            )
        await asyncio.to_thread(_write_pending_parse_prompt_state, fallback)
        return _to_widget_tool_result(_promote_paper_selection_app(fallback))

    if len(candidates) <= AUTO_PARSE_SAVED_PDF_LIMIT:
        if parse_execution_name == "sync":
            parse_result = await _parse_selected_papers_fn(
                selection_token=parse_session["selection_token"],
                selected_indices=selected_indices_arg,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
            )
            return {
                **parse_result,
                "interaction": "auto_parse_saved_pdfs",
                "selection_token": parse_session["selection_token"],
                "download_selection_token": selection_token,
                "papers": candidates,
                "parse_ready_total": len(selectable),
                "selected_indices": selected_indices,
                "recommended_tool": "get_parsed_paper",
                "recommended_selected_indices": selected_indices_arg,
                "auto_parse_limit": AUTO_PARSE_SAVED_PDF_LIMIT,
                "parse_execution": parse_execution_name,
                "message": (
                    f"Saved {len(candidates)} PDF(s), which is at or below "
                    f"the auto-parse limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. "
                    "Parsed all saved PDFs."
                ),
            }

        if _submit_parse_job_fn is None:
            return {
                **fallback,
                "status": "error",
                "message": "submit_parse_job is not available.",
            }
        parse_job = await _submit_parse_job_fn(
            parse_fn=_parse_selected_papers_fn,
            selection_token=parse_session["selection_token"],
            selected_indices=selected_indices_arg,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        return {
            **fallback,
            "status": (
                parse_job.get("status", "submitted")
                if isinstance(parse_job, dict)
                else "submitted"
            ),
            "interaction": "auto_parse_saved_pdfs",
            "selected_indices": selected_indices,
            "recommended_tool": "get_parse_job_status",
            "recommended_selected_indices": selected_indices_arg,
            "auto_parse_limit": AUTO_PARSE_SAVED_PDF_LIMIT,
            "parse_job": parse_job,
            "message": (
                f"Saved {len(candidates)} PDF(s), which is at or below "
                f"the auto-parse limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. "
                "Submitted a MinerU parse job."
            ),
        }

    if len(candidates) > AUTO_PARSE_SAVED_PDF_LIMIT:
        fallback["message"] = (
            f"Saved {len(candidates)} PDFs, which is above the auto-parse "
            f"limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. Use the checkbox UI "
            "or numbered indices to choose PDFs for MinerU."
        )
        fallback["parse_decision_required"] = True
        fallback["requires_user_parse_decision"] = True
        await _attach_local_selection_ui(
            fallback,
            selection_token=parse_session["selection_token"],
            papers=candidates,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            # _selection_ui_should_open suppresses browser for widget-capable hosts
            force_open=True,
        )

    if ctx is None:
        prompted = {
            **fallback,
            "parse_decision_required": True,
            "requires_user_parse_decision": True,
            "recommended_tool": PAPER_SELECTION_WIDGET_TOOL,
            "recommended_selected_indices": "",
        }
        await asyncio.to_thread(_write_pending_parse_prompt_state, prompted)
        return _to_widget_tool_result(_promote_paper_selection_app(prompted))

    options = [_elicitation_option_label(candidate) for candidate in selectable]
    schema = _build_paper_selection_schema(options)
    try:
        elicitation = await ctx.elicit(
            message="More than 10 PDFs were saved. Select PDFs for MinerU "
            "PDF parsing.",
            schema=schema,
        )
    except Exception as exc:
        return {
            **fallback,
            "message": f"Elicitation request failed: {exc}",
        }

    if getattr(elicitation, "action", "") != "accept":
        return {
            **fallback,
            "status": "elicitation_not_accepted",
            "elicitation_action": getattr(elicitation, "action", ""),
            "message": (
                "User declined or cancelled parsing. "
                "Use the checkbox UI or numbered indices if needed."
            ),
        }

    selected_values = getattr(
        getattr(elicitation, "data", None), "selected_papers", []
    )
    try:
        selected_indices = _parse_elicitation_selected_indices(
            selected_values, len(candidates)
        )
    except ValueError as exc:
        return {
            **fallback,
            "status": "invalid_elicitation_selection",
            "message": str(exc),
        }

    if not selected_indices:
        return {
            **fallback,
            "status": "no_selection",
            "message": (
                "No PDFs were selected. Use the checkbox UI or numbered "
                "indices if needed."
            ),
        }

    selected_indices_arg = ",".join(str(index) for index in selected_indices)
    if parse_execution_name == "sync":
        parse_result = await _parse_selected_papers_fn(
            selection_token=parse_session["selection_token"],
            selected_indices=selected_indices_arg,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        return {
            **parse_result,
            "interaction": "elicitation",
            "selection_token": parse_session["selection_token"],
            "download_selection_token": selection_token,
            "papers": candidates,
            "selected_indices": selected_indices,
            "recommended_tool": "get_parsed_paper",
            "recommended_selected_indices": selected_indices_arg,
            "parse_execution": parse_execution_name,
        }

    if _submit_parse_job_fn is None:
        return {
            **fallback,
            "status": "error",
            "message": "submit_parse_job is not available.",
        }
    parse_job = await _submit_parse_job_fn(
        parse_fn=_parse_selected_papers_fn,
        selection_token=parse_session["selection_token"],
        selected_indices=selected_indices_arg,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )
    return {
        **fallback,
        "status": (
            parse_job.get("status", "submitted")
            if isinstance(parse_job, dict)
            else "submitted"
        ),
        "interaction": "elicitation",
        "selected_indices": selected_indices,
        "recommended_tool": "get_parse_job_status",
        "recommended_selected_indices": selected_indices_arg,
        "parse_job": parse_job,
        "message": "Submitted a MinerU parse job for the selected PDFs.",
    }


# ===========================================================================
# _pre_download_selection_prompt
# ===========================================================================

async def _pre_download_selection_prompt(
    *,
    selection_token: str,
    session: Dict[str, Any],
    indices: List[int],
    save_path: str,
    use_scihub: bool,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    parse_execution: str = "background",
    custom_save_path_confirmed: bool = False,
    selection_semantics: str = SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
    skip_local_ui: bool = False,
) -> Dict[str, Any]:
    """Pre-download selection prompt for large batches.

    On repeated calls with the same *selection_token*, this function
    reuses a previously-created pre-download parse session so that
    only ONE local browser page is opened per download context.
    """
    semantics = _selection_semantics_name(selection_semantics)
    source_papers = session.get("papers", [])
    if not isinstance(source_papers, list):
        source_papers = []

    selected_papers: List[Dict[str, Any]] = []
    for index in indices:
        if 1 <= index <= len(source_papers) and isinstance(
            source_papers[index - 1], dict
        ):
            selected_papers.append(source_papers[index - 1])

    # ── Reuse an existing pending pre-download session when one exists ──
    _reused_session = False
    download_session = await asyncio.to_thread(
        _cache_get_search_session, selection_token
    )
    pending_token = (
        download_session.get("metadata", {})
        .get("pending_pre_download_token", "")
        .strip()
        if isinstance(download_session, dict)
        else ""
    )
    if pending_token:
        existing = await asyncio.to_thread(
            _cache_get_search_session, pending_token
        )
        # Also verify the parse prompt hasn't already been resolved
        parse_prompt_state = await asyncio.to_thread(
            _cache_read_parse_prompt_state, selection_token
        )
        if (
            isinstance(parse_prompt_state, dict)
            and str(parse_prompt_state.get("state") or "")
            in TERMINAL_PARSE_PROMPT_STATES
        ):
            # Prompt was already dismissed or timed out — clear stale
            # reference so a fresh session/page can be created.
            await asyncio.to_thread(
                _cache_update_search_session_metadata,
                selection_token,
                {"pending_pre_download_token": ""},
            )
            pending_token = ""
        elif (
            isinstance(existing, dict)
            and existing.get("selection_token") == pending_token
        ):
            # Reuse the existing session — don't create a new one or
            # open another browser page.
            parse_session = existing
            _reused_session = True
        else:
            # Stale reference — clear it
            await asyncio.to_thread(
                _cache_update_search_session_metadata,
                selection_token,
                {"pending_pre_download_token": ""},
            )
            pending_token = ""
            parse_session = None
    else:
        parse_session = None

    if parse_session is None:
        parse_session = await asyncio.to_thread(
            _cache_create_search_session,
            session.get("query", ""),
            session.get("sources", ""),
            selected_papers,
            {
                "interaction": "pre_download_selection",
                "trigger": "pre_download_batch_threshold",
                "download_selection_token": selection_token,
                "save_path": resolve_save_path(save_path),
                "selected_source_indices": indices,
                "selection_semantics": semantics,
                "parse_execution": _workflow_parse_execution_name(parse_execution),
            },
        )
        # Record the back-reference so subsequent calls reuse this session
        try:
            await asyncio.to_thread(
                _cache_update_search_session_metadata,
                selection_token,
                {
                    "pending_pre_download_token": parse_session["selection_token"],
                },
            )
        except Exception:
            logger.debug(
                "Failed to store pending_pre_download_token on session %s",
                selection_token,
                exc_info=True,
            )

    candidates = [
        _paper_parse_candidate(paper, index + 1)
        for index, paper in enumerate(selected_papers)
    ]
    selectable = [
        candidate for candidate in candidates if candidate.get("parse_ready")
    ]
    action_message = (
        "Select papers before download; only selected papers will be saved."
        if semantics == SELECTION_SEMANTICS_DOWNLOAD_ONLY
        else (
            "Select papers before download; only selected papers will be "
            "saved and parsed."
        )
    )
    selection_message = (
        f"{len(candidates)} papers were requested, above the batch selection "
        f"limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. Select the papers to download."
        if semantics == SELECTION_SEMANTICS_DOWNLOAD_ONLY
        else (
            f"{len(candidates)} papers were requested, above the auto-parse "
            f"limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. Select the papers to "
            "download and parse with MinerU."
        )
    )
    prompt: Dict[str, Any] = {
        "status": "ok" if selectable else "no_parse_ready_papers",
        "interaction": "pre_download_checkbox_selection",
        "selection_token": parse_session["selection_token"],
        "download_selection_token": selection_token,
        "instructions": f"More than 10 papers were requested. {action_message}",
        "papers": candidates,
        "total": len(candidates),
        "parse_ready_total": len(selectable),
        "save_path": resolve_save_path(save_path),
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "parse_execution": _workflow_parse_execution_name(parse_execution),
        "parse_decision_required": True,
        "requires_user_parse_decision": True,
        "recommended_tool": PAPER_SELECTION_WIDGET_TOOL,
        "recommended_selected_indices": "",
        "selection_semantics": semantics,
        "message": selection_message,
    }
    prompt["app"] = _paper_selection_app_prompt(
        selection_token=parse_session["selection_token"],
        papers=candidates,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        selection_semantics=semantics,
        parse_execution=parse_execution,
    )
    if not skip_local_ui and not _reused_session:
        await _attach_local_selection_ui(
            prompt,
            selection_token=parse_session["selection_token"],
            papers=candidates,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            # _selection_ui_should_open suppresses browser for widget-capable hosts
            force_open=True,
            selection_semantics=semantics,
            parse_execution=parse_execution,
        )
    return _promote_paper_selection_app(_prefer_local_selection_surface(prompt))


# ===========================================================================
# Module-level download helper (importable by ui/server.py)
# ===========================================================================


async def _run_download_selected_papers(
    *,
    selection_token: str,
    selected_indices: str = "",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    download_strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
    concurrency: int = 0,
    parse_execution: str = "none",
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
    large_batch_selection: str = "never",
    bypass_large_batch_selection: bool = False,
    _caller: str = "unknown",
) -> Dict[str, Any]:
    """Download papers from a saved selection session without parsing.

    This is a module-level function that can be imported by external code
    (e.g. the local browser selection UI) as well as used internally by the
    MCP tool wrappers.  It does NOT trigger large-batch checks or parse
    policies — those are handled by the caller.

    ``bypass_large_batch_selection`` is **only** honoured when ``_caller`` is
    a whitelisted internal path (local browser UI or confirmed-download handler).
    Otherwise the bypass flag is silently ignored to prevent programmatic
    gate-skipping by LLM tool invocations.
    """
    # ── Gate: only trusted internal callers may bypass large-batch checks ──
    _TRUSTED_BYPASS_CALLERS = frozenset({
        "local_browser_ui",
        "confirmed_download_handler",
    })
    if bypass_large_batch_selection and _caller not in _TRUSTED_BYPASS_CALLERS:
        bypass_large_batch_selection = False
    explicit_selection = _selected_indices_was_explicit(selected_indices)
    invalid_save_path = _invalid_mcp_save_path(
        save_path,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )
    if invalid_save_path:
        return invalid_save_path

    save_path = resolve_save_path(save_path)
    session = await asyncio.to_thread(_cache_get_search_session, selection_token)
    if not session:
        return {
            "status": "not_found",
            "selection_token": selection_token,
            "message": "Search session not found. Run crawl_papers_for_selection again.",
        }

    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []

    if not explicit_selection:
        return {
            "status": "no_selection",
            "selection_token": selection_token,
            "message": "No papers were selected. Choose papers in the selector before downloading.",
            "total": len(papers),
            "downloaded": 0,
            "skipped_existing": 0,
            "skipped": 0,
            "failed": 0,
        }

    try:
        indices = _parse_selected_indices(selected_indices, len(papers))
    except ValueError as exc:
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "message": str(exc),
            "total": len(papers),
        }

    download_concurrency = (
        concurrency
        if concurrency and concurrency > 0
        else _env_int(DOWNLOAD_CONCURRENCY_ENV, 8, minimum=1)
    )
    semaphore = asyncio.Semaphore(download_concurrency)
    download_timeout = _env_float(DOWNLOAD_TIMEOUT_ENV, 30.0, minimum=1.0)

    async def _limited(
        index: int, shared_client: httpx.AsyncClient
    ) -> Dict[str, Any]:
        async with semaphore:
            paper = papers[index - 1]
            if not isinstance(paper, dict):
                return {
                    "index": index,
                    "status": "skipped",
                    "message": "Stored search result is not a paper dictionary.",
                }
            return await _download_selected_session_paper_wrapper(
                paper=paper,
                index=index,
                save_path=save_path,
                use_scihub=use_scihub,
                client=shared_client,
                download_strategy=download_strategy,
                use_libgen=use_libgen,
                libgen_base_url=libgen_base_url,
            )

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=download_timeout
    ) as shared_client:
        results = await asyncio.gather(
            *[_limited(index, shared_client) for index in indices]
        )

    downloaded = sum(
        1 for result in results if result.get("status") == "downloaded"
    )
    skipped_existing = sum(
        1 for result in results if result.get("status") == "skipped_existing"
    )
    skipped = sum(
        1 for result in results if result.get("status") == "skipped"
    )
    failed = len(results) - downloaded - skipped_existing - skipped
    status = (
        "ok"
        if failed == 0
        else "partial"
        if downloaded or skipped_existing
        else "failed"
    )

    # Save manifest for resume support
    if selection_token:
        try:
            manifest_path = _download_manifest_path(save_path, selection_token)
            manifest = {
                "status": status,
                "selection_token": selection_token,
                "query": session.get("query", ""),
                "sources": session.get("sources", ""),
                "selected_indices": indices,
                "save_path": save_path,
                "use_scihub": use_scihub,
                "download_strategy": download_strategy or "env_default",
                "use_libgen": use_libgen,
                "download_concurrency": download_concurrency,
                "created_at": utc_now(),
                "results": results,
                "total": len(results),
                "downloaded": downloaded,
                "skipped_existing": skipped_existing,
                "skipped": skipped,
                "failed": failed,
            }
            await asyncio.to_thread(write_json, manifest_path, manifest)
        except Exception:
            manifest_path = ""
    else:
        manifest_path = ""

    successful_pdf_count = downloaded + skipped_existing
    parse_prompt: Dict[str, Any] = {}
    if successful_pdf_count > 0:
        parse_prompt = await _parse_prompt_for_download_results(
            selection_token=selection_token,
            session=session,
            results=results,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            parse_execution=parse_execution,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )

    response = {
        "status": status,
        "selection_token": selection_token,
        "selected_indices": indices,
        "results": results,
        "total": len(results),
        "downloaded": downloaded,
        "skipped_existing": skipped_existing,
        "successful_pdf_count": successful_pdf_count,
        "skipped": skipped,
        "failed": failed,
        "manifest_path": manifest_path,
        "parse_execution": _workflow_parse_execution_name(parse_execution),
    }
    if parse_prompt:
        response["parse_prompt"] = parse_prompt
        response["message"] = parse_prompt.get("message", "")
        if isinstance(parse_prompt.get("app"), dict):
            response["app"] = parse_prompt["app"]
    return (
        _promote_paper_selection_app(response)
        if _should_promote_paper_selection_app(parse_prompt)
        else response
    )


# ===========================================================================
# Module-level reference — set by register_orchestration_tools so other
# modules (CLI, tests) can call the registered tool functions directly.
# ===========================================================================

search_papers = None
search_papers_for_parsing = None
crawl_papers_for_selection = None
search_papers_with_elicitation = None
download_with_fallback = None
download_selected_papers = None
resume_download = None
crawl_download_parse_papers = None
paper_research_workflow = None


def _set_module_functions(**kwargs):
    """Store registered tool functions at module level."""
    import sys
    mod = sys.modules[__name__]
    for name, fn in kwargs.items():
        setattr(mod, name, fn)


# ===========================================================================
# MAIN REGISTRATION FUNCTION
# ===========================================================================

def register_orchestration_tools(mcp, searchers):
    """Register all high-level orchestration MCP tools on *mcp*.

    Parameters
    ----------
    mcp : FastMCP
        The server instance.
    searchers : dict
        Dict mapping source name (str) to searcher instance, e.g.:
        ``{"arxiv": arxiv_searcher, "semantic": semantic_searcher, ...}``.
    """

    arxiv_searcher = searchers.get("arxiv")
    semantic_searcher = searchers.get("semantic")
    # (other searchers accessed via the dict where needed)

    # =======================================================================
    #  search_papers
    # =======================================================================
    @mcp.tool()
    async def search_papers(
        query: str,
        max_results_per_source: int = 5,
        sources: str = "",
        year: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Unified top-level search across all configured academic platforms.

        Args:
            query: Search query string.
            max_results_per_source: Max results to fetch from each selected source.
            sources: Comma-separated source names or 'all'.
                Available: arxiv,pubmed,biorxiv,medrxiv,google_scholar,iacr,semantic,crossref,openalex,pmc,core,europepmc,dblp,openaire,citeseerx,doaj,base,zenodo,hal,ssrn,unpaywall
            year: Optional year filter for Semantic Scholar only.
        Returns:
            Aggregated dictionary with per-source stats, errors, and deduplicated papers.
        """
        started = time.perf_counter()
        selected_sources = _parse_sources(sources)
        cache_key = _search_cache_key(query, max_results_per_source, sources, year)
        cached = _cached_search_result(cache_key)
        if cached is not None:
            return cached

        if not selected_sources:
            return {
                "query": query,
                "sources_requested": sources,
                "sources_used": [],
                "source_results": {},
                "errors": {"sources": "No valid sources selected."},
                "papers": [],
                "total": 0,
            }

        from ..engine.search import async_search as _async_search

        # Build kwargs per source for non-standard signatures
        _extra_kwargs: Dict[str, Dict[str, Any]] = {
            "semantic": {"year": year} if year is not None else {},
            "iacr": {"fetch_details": False},
            "arxiv": {"sort_by": "relevance", "sort_order": "descending",
                       "timeout": _env_float("ARXIV_TIMEOUT_SECONDS", 6.0, minimum=1.0),
                       "max_attempts": _env_int("ARXIV_MAX_ATTEMPTS", 1, minimum=1)},
            "pubmed": {"sort": "relevance"},
        }

        task_map = {}
        for source in selected_sources:
            searcher = searchers.get(source)
            if searcher is None:
                continue
            kwargs = _extra_kwargs.get(source, {})
            task_map[source] = _async_search(searcher, query, max_results_per_source, **kwargs)

        source_names = list(task_map.keys())
        base_timeout = _env_float(SEARCH_SOURCE_TIMEOUT_ENV, 12.0, minimum=0.0)
        overall_timeout = _env_float(SEARCH_TIMEOUT_ENV, 18.0, minimum=0.0)
        # ── Adaptive per-source timeout ───────────────────────────────
        # Reliable sources (arxiv: 95, openalex: 82) get full budget.
        # Less reliable sources (google_scholar: 20, ssrn: 24) get
        # proportionally less time so they don't steal budget from
        # fast/reliable sources that produce actual PDFs.
        source_tasks = []
        for source in source_names:
            reliability = _source_reliability_score(source)
            if reliability >= 60:
                timeout = base_timeout
            elif reliability >= 40:
                timeout = base_timeout * 0.75
            elif reliability >= 20:
                timeout = base_timeout * 0.5
            else:
                timeout = max(base_timeout * 0.3, 3.0)
            source_tasks.append(
                asyncio.create_task(
                    _search_source_with_timeout(source, task_map[source], timeout)
                )
            )
        try:
            if overall_timeout > 0:
                source_outputs = await asyncio.wait_for(
                    asyncio.gather(*source_tasks, return_exceptions=True),
                    timeout=overall_timeout,
                )
            else:
                source_outputs = await asyncio.gather(*source_tasks, return_exceptions=True)
        except asyncio.TimeoutError:
            source_outputs = []
            for source, task in zip(source_names, source_tasks):
                if task.done():
                    if task.cancelled():
                        source_outputs.append(
                            {
                                "source": source,
                                "output": [],
                                "error": f"overall search timed out after {overall_timeout:g}s",
                                "timed_out": True,
                            }
                        )
                        continue
                    try:
                        source_outputs.append(task.result())
                    except Exception as exc:
                        source_outputs.append({"source": source, "output": [], "error": str(exc)})
                else:
                    task.cancel()
                    source_outputs.append(
                        {
                            "source": source,
                            "output": [],
                            "error": f"overall search timed out after {overall_timeout:g}s",
                            "timed_out": True,
                        }
                    )
            await asyncio.gather(*source_tasks, return_exceptions=True)

        source_results: Dict[str, int] = {}
        errors: Dict[str, str] = {}
        source_timings: Dict[str, float] = {}
        timed_out_sources: List[str] = []
        merged_papers: List[Dict[str, Any]] = []

        for source_name, output in zip(source_names, source_outputs):
            if isinstance(output, Exception):
                errors[source_name] = str(output)
                source_results[source_name] = 0
                continue

            if not isinstance(output, dict):
                errors[source_name] = f"unexpected search result: {output!r}"
                source_results[source_name] = 0
                continue

            papers = output.get("output", []) or []
            source_results[source_name] = len(papers)
            if output.get("error"):
                errors[source_name] = str(output["error"])
            if output.get("timed_out"):
                timed_out_sources.append(source_name)
            if "elapsed_seconds" in output:
                source_timings[source_name] = float(output["elapsed_seconds"])

            for paper in papers:
                if not paper.get("source"):
                    paper["source"] = source_name
                merged_papers.append(paper)

        deduped_papers = _dedupe_papers(merged_papers, query=query)

        # ── Post-filter by year ──────────────────────────────────────
        # Only semantic natively supports year filtering; other sources
        # return all years.  Apply a client-side filter so that the
        # `year` parameter works reliably across every source.
        if year is not None:
            try:
                target_year = str(year).strip()
            except Exception:
                target_year = ""
            if target_year:
                before = len(deduped_papers)
                deduped_papers = [
                    p for p in deduped_papers
                    if _paper_year(p) == target_year
                ]
                logger.debug(
                    "Year filter %s: %d → %d papers",
                    target_year, before, len(deduped_papers),
                )

        result = {
            "query": query,
            "sources_requested": sources,
            "sources_used": source_names,
            "source_priority": [
                {
                    "source": source,
                    "reliability": _source_reliability(source),
                }
                for source in _rank_sources_by_reliability(source_names)
            ],
            "source_results": source_results,
            "errors": errors,
            "timed_out_sources": timed_out_sources,
            "source_timings": source_timings,
            "papers": deduped_papers,
            "total": len(deduped_papers),
            "raw_total": len(merged_papers),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "cache": {
                "hit": False,
                "ttl_seconds": _env_int(SEARCH_CACHE_TTL_ENV, 300, minimum=0),
            },
        }
        _store_search_result(cache_key, result)
        return result

    # =======================================================================
    #  search_papers_for_parsing
    # =======================================================================
    @mcp.tool()
    async def search_papers_for_parsing(
        query: str,
        max_results_per_source: int = 5,
        sources: str = "",
        year: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search papers, persist a numbered selection session, and return parse candidates.

        Use this when the MCP client cannot show elicitation/App checkbox UI. The
        caller can present the returned numbered list, then call
        parse_selected_papers with the selection_token and indices like "1,3,5".
        """
        search_result = await search_papers(
            query=query,
            max_results_per_source=max_results_per_source,
            sources=sources,
            year=year,
        )
        return _to_widget_tool_result(
            await _create_paper_selection_result(
                query=query,
                max_results_per_source=max_results_per_source,
                sources=sources,
                year=year,
                search_result=search_result,
                interaction="backend_session_numbered_selection",
                action_tool="parse_selected_papers",
                action_verb="parse",
            )
        )

    # =======================================================================
    #  crawl_papers_for_selection
    # =======================================================================
    @mcp.tool()
    async def crawl_papers_for_selection(
        query: str,
        max_results_per_source: int = 5,
        sources: str = "",
        year: Optional[str] = None,
        ranking_profile: str = "",
        requested_count: int = 0,
    ) -> Dict[str, Any]:
        """Search papers and persist a checkbox/numbered selection session without downloading or parsing.

        Use ranking_profile='agent-skill' for LLM-agent skill/library/retrieval/security topics.
        The response always includes a selection_token, MCP App checkbox prompt, and numbered_fallback.

        When *requested_count* is set and the first round of search does not
        return enough downloadable papers, the tool automatically retries with
        progressively broader source profiles and higher oversampling until the
        target is met or all configured rounds are exhausted.
        """
        effective_sources = sources
        is_agent_skill = (
            _ranking_profile_name(ranking_profile) == AGENT_SKILL_RANKING_PROFILE
        )
        if not (effective_sources or "").strip() and is_agent_skill:
            effective_sources = "agent-skill-fast"

        # ── Progressive retry rounds ─────────────────────────────────
        # Each round specifies (source_profile, oversample_multiplier).
        # When requested_count > 0 and the first round under-yields,
        # progressively broader profiles are tried until the target is
        # met or all rounds are exhausted.  This was previously gated to
        # agent-skill only but now applies to all profiles.
        RETRY_ROUNDS: List[tuple] = [
            (effective_sources, 2.5),
        ]
        if requested_count > 0:
            if is_agent_skill:
                RETRY_ROUNDS.extend([
                    ("agent-skill-broad", 5.0),
                    ("agent-skill-broad", 3.0),
                ])
            else:
                # Generic progressive broadening: fast → fast (higher oversample)
                # We intentionally avoid "deep" here because it includes
                # low-reliability sources (google_scholar, citeseerx, ssrn,
                # base) that are prone to anti-bot blocking and timeouts.
                # The second round uses "fast" with higher oversampling
                # instead of broadening to unreliable sources.
                RETRY_ROUNDS.extend([
                    ("fast", 5.0),
                ])

        all_ranked_papers: List[Dict[str, Any]] = []
        seen_keys: set = set()
        combined_search_result: Optional[Dict[str, Any]] = None
        target = max(requested_count, 1) if requested_count > 0 else 0

        for round_idx, (round_sources, multiplier) in enumerate(RETRY_ROUNDS):
            # ── Check if we already have enough downloadable papers ──
            if round_idx > 0 and target > 0:
                ready = _count_download_ready_papers(all_ranked_papers)
                logger.info(
                    "crawl_papers_for_selection round %d: %d download-ready, need %d",
                    round_idx + 1, ready, target,
                )
                if ready >= target:
                    break

            # ── Oversample ──────────────────────────────────────────
            num_sources = max(len(_parse_sources(round_sources)), 1)
            if requested_count > 0:
                oversampled = math.ceil(requested_count * multiplier / num_sources)
                effective_max = max(max_results_per_source, oversampled)
            else:
                effective_max = max_results_per_source

            # ── Search (different sources param ensures cache miss) ──
            search_result = await search_papers(
                query=query,
                max_results_per_source=effective_max,
                sources=round_sources,
                year=year,
            )

            # ── Dedup and accumulate ────────────────────────────────
            round_papers = search_result.get("papers", [])
            if not isinstance(round_papers, list):
                round_papers = []
            new_papers: List[Dict[str, Any]] = []
            for paper in round_papers:
                key = _paper_unique_key(paper)
                if key not in seen_keys:
                    seen_keys.add(key)
                    new_papers.append(paper)
            all_ranked_papers.extend(new_papers)

            # ── Merge result metadata ───────────────────────────────
            if combined_search_result is None:
                combined_search_result = search_result
            else:
                combined_search_result = _merge_search_results(
                    combined_search_result, search_result
                )

            # ── Stop early if no new papers found ───────────────────
            if not new_papers:
                logger.info(
                    "crawl_papers_for_selection round %d: no new papers, stopping",
                    round_idx + 1,
                )
                break

        return _to_widget_tool_result(
            await _create_paper_selection_result(
                query=query,
                max_results_per_source=max_results_per_source,
                sources=effective_sources,
                year=year,
                search_result=combined_search_result,
                interaction="crawl_papers_for_selection",
                ranking_profile=ranking_profile,
                action_tool="download_selected_papers",
                action_verb="download",
                selection_semantics=SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                requested_count=requested_count,
            )
        )

    # =======================================================================
    #  search_papers_with_elicitation
    # =======================================================================
    @mcp.tool()
    async def search_papers_with_elicitation(
        query: str,
        max_results_per_source: int = 5,
        sources: str = "",
        year: Optional[str] = None,
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
        ctx: Optional[Context] = None,
    ) -> Dict[str, Any]:
        """Search papers, ask the MCP client for a multi-select choice, then parse.

        MCP clients with elicitation support, such as VS Code Copilot Agent Mode,
        can render the returned schema as a native multi-select control. Clients
        without elicitation support receive the same backend session and numbered
        paper list used by search_papers_for_parsing/parse_selected_papers.
        """
        session_result = await search_papers_for_parsing(
            query=query,
            max_results_per_source=max_results_per_source,
            sources=sources,
            year=year,
        )
        # ── Unwrap ToolResult from search_papers_for_parsing ──────────────
        session_result, _ = _unwrap_tool_result(session_result)
        if not isinstance(session_result, dict):
            session_result = {}
        candidates = session_result.get("papers", [])
        if not isinstance(candidates, list):
            candidates = []

        selectable = [
            candidate for candidate in candidates if candidate.get("parse_ready")
        ]
        if not selectable:
            return {
                **session_result,
                "status": "no_parse_ready_papers",
                "interaction": "backend_session_numbered_selection",
                "message": (
                    "No parse-ready papers were found. Use the returned session "
                    "for inspection or search again."
                ),
            }

        if ctx is None:
            return {
                **session_result,
                "status": "elicitation_unavailable",
                "interaction": "backend_session_numbered_selection",
                "message": (
                    "MCP context was not available, so no elicitation request "
                    "could be sent."
                ),
            }

        options = [_elicitation_option_label(candidate) for candidate in selectable]
        schema = _build_paper_selection_schema(options)

        try:
            elicitation = await ctx.elicit(
                message=(
                    "Select papers for MinerU PDF parsing. "
                    "If the client does not show a checkbox or multi-select UI, "
                    "use the returned selection_token with numbered indices."
                ),
                schema=schema,
            )
        except Exception as exc:
            return {
                **session_result,
                "status": "elicitation_unavailable",
                "interaction": "backend_session_numbered_selection",
                "message": f"Elicitation request failed: {exc}",
            }

        if getattr(elicitation, "action", "") != "accept":
            return {
                **session_result,
                "status": "elicitation_not_accepted",
                "interaction": "backend_session_numbered_selection",
                "elicitation_action": getattr(elicitation, "action", ""),
                "message": (
                    "User declined or cancelled the selection. Use "
                    "parse_selected_papers with numbered indices if needed."
                ),
            }

        selected_values = getattr(
            getattr(elicitation, "data", None), "selected_papers", []
        )
        try:
            selected_indices = _parse_elicitation_selected_indices(
                selected_values, len(candidates)
            )
        except ValueError as exc:
            return {
                **session_result,
                "status": "invalid_elicitation_selection",
                "interaction": "backend_session_numbered_selection",
                "message": str(exc),
            }

        if not selected_indices:
            return {
                **session_result,
                "status": "no_selection",
                "interaction": "backend_session_numbered_selection",
                "message": (
                    "No papers were selected. Use parse_selected_papers with "
                    "numbered indices if needed."
                ),
            }

        # Lazy-import parse_selected_papers from .core
        from .core import _run_parse_selected_papers as parse_selected_papers  # noqa: PLC0415

        parse_result = await parse_selected_papers(
            selection_token=session_result["selection_token"],
            selected_indices=",".join(str(index) for index in selected_indices),
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
        )
        return {
            **parse_result,
            "interaction": "elicitation",
            "selection_token": session_result["selection_token"],
            "selected_indices": selected_indices,
            "search": {
                "query": query,
                "sources_requested": sources,
                "sources_used": session_result.get("sources_used", []),
                "source_results": session_result.get("source_results", {}),
                "errors": session_result.get("errors", {}),
                "total": session_result.get("total", 0),
                "parse_ready_total": session_result.get("parse_ready_total", 0),
            },
        }

    # =======================================================================
    #  download_with_fallback
    # =======================================================================
    @mcp.tool()
    async def download_with_fallback(
        source: str,
        paper_id: str,
        doi: str = "",
        title: str = "",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        scihub_base_url: str = "https://sci-hub.se",
        download_strategy: str = "",
        use_libgen: Optional[bool] = None,
        libgen_base_url: str = "",
        custom_save_path_confirmed: bool = False,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Try source-native/OA fallback download, then ask whether to parse saved PDFs."""
        invalid_save_path = _invalid_mcp_save_path(
            save_path,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        if invalid_save_path:
            return invalid_save_path

        routed_source, routed_paper_id, routed_doi = _source_from_identifier(
            source.strip().lower(),
            paper_id,
            doi,
        )
        result = await _download_with_fallback_path_wrapper(
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            use_scihub=use_scihub,
            scihub_base_url=scihub_base_url,
            download_strategy=download_strategy,
            use_libgen=use_libgen,
            libgen_base_url=libgen_base_url,
            _searchers=searchers,
        )
        if isinstance(result, dict) and result.get("status") == "ok":
            pdf_path = result.get("pdf_path", "")
            if pdf_path and os.path.exists(pdf_path):
                legal_status = "source_native_or_open_access"
                if use_scihub and "sci" in Path(pdf_path).name.lower():
                    legal_status = "user_opt_in_scihub"

                from ..engine.parse import _after_saved_pdf

                return await _after_saved_pdf(
                    pdf_path,
                    source=routed_source or source.strip().lower(),
                    paper_id=routed_paper_id or paper_id,
                    doi=routed_doi or doi,
                    title=title,
                    save_path=save_path,
                    downloader="download_with_fallback",
                    legal_status=legal_status,
                    ctx=ctx,
                    _attach_local_selection_ui_fn=_attach_local_selection_ui,
                )
        # Legacy string return from older engine or scihub path
        if isinstance(result, str) and os.path.exists(result):
            legal_status = "source_native_or_open_access"
            if use_scihub and "sci" in Path(result).name.lower():
                legal_status = "user_opt_in_scihub"

            from ..engine.parse import _after_saved_pdf

            return await _after_saved_pdf(
                result,
                source=routed_source or source.strip().lower(),
                paper_id=routed_paper_id or paper_id,
                doi=routed_doi or doi,
                title=title,
                save_path=save_path,
                downloader="download_with_fallback",
                legal_status=legal_status,
                ctx=ctx,
                _attach_local_selection_ui_fn=_attach_local_selection_ui,
            )
        return result

    # =======================================================================
    #  download_selected_papers
    # =======================================================================
    @mcp.tool(
        meta={
            "ui": {
                "resourceUri": PAPER_SELECTION_WIDGET_URI,
                "visibility": ["model", "app"],
            },
            "ui/resourceUri": PAPER_SELECTION_WIDGET_URI,
            "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
            "openai/widgetAccessible": True,
            "openai/toolInvocation/invoking": "Downloading selected papers...",
            "openai/toolInvocation/invoked": "Paper download finished.",
        },
        structured_output=True,
    )
    async def download_selected_papers(
        selection_token: str,
        selected_indices: str = "",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        download_strategy: str = "",
        use_libgen: Optional[bool] = None,
        libgen_base_url: str = "",
        concurrency: int = 0,
        parse_execution: str = "none",
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
        custom_save_path_confirmed: bool = False,
        large_batch_selection: str = "auto",
        bypass_large_batch_selection: bool = False,
        resume: bool = False,
        ctx: Optional[Context] = None,
    ) -> Dict[str, Any]:
        """Download papers from a saved selection session.

        Set resume=True to skip already-downloaded papers from a previous partial run.
        MinerU parsing is not started unless parse_execution is explicitly set.

        When more than 10 papers are requested, a real checkbox UI confirmation
        is required — this tool will return ``selection_required`` with a
        local browser URL or MCP App widget prompt.  Programmatic bypass is
        not available through this public entry point.
        """
        invalid_save_path = _invalid_mcp_save_path(
            save_path,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        if invalid_save_path:
            return invalid_save_path

        save_path = resolve_save_path(save_path)
        save_path_meta = _mcp_save_path_metadata(
            save_path,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        session = await asyncio.to_thread(_cache_get_search_session, selection_token)
        if not session:
            return {
                "status": "not_found",
                "selection_token": selection_token,
                "message": "Search session not found. Run crawl_papers_for_selection again.",
            }

        papers = session.get("papers", [])
        if not isinstance(papers, list):
            papers = []

        if not _selected_indices_was_explicit(selected_indices):
            candidates = [
                _paper_parse_candidate(paper, index + 1)
                for index, paper in enumerate(papers)
            ]
            surface = _selection_surface_policy(force_open=True)
            prompt = {
                "status": "selection_required",
                "selection_token": selection_token,
                "query": session.get("query", ""),
                "sources": session.get("sources", ""),
                **save_path_meta,
                "papers": candidates,
                "total": len(papers),
                "downloaded": 0,
                "skipped_existing": 0,
                "skipped": 0,
                "failed": 0,
                "recommended_tool": PAPER_SELECTION_WIDGET_TOOL,
                "recommended_selected_indices": "",
                "selection_semantics": SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                "detected_host": surface.get("detected_host", "unknown"),
                "app_widget_supported": surface.get("app_widget_supported", False),
                "selection_surface": surface,
                "message": (
                    "No papers were selected. Render the paper selector and wait "
                    "for a user-selected checkbox submission before downloading."
                ),
            }
            prompt["app"] = _paper_selection_app_prompt(
                selection_token=selection_token,
                papers=candidates,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
                selection_semantics=SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                parse_execution="none",
            )
            return _to_widget_tool_result(_promote_paper_selection_app(prompt))

        try:
            indices = _parse_selected_indices(selected_indices, len(papers))
        except ValueError as exc:
            return {
                "status": "invalid_selection",
                "selection_token": selection_token,
                "message": str(exc),
                "total": len(papers),
            }

        # ── Resume / checkpoint: skip already-downloaded papers ──────────
        original_indices = list(indices)
        if resume:
            download_state = await asyncio.to_thread(
                _cache_read_session_download_state, selection_token
            )
            if isinstance(download_state, dict) and isinstance(
                download_state.get("results"), dict
            ):
                completed = {
                    int(k)
                    for k, v in download_state["results"].items()
                    if isinstance(v, dict)
                    and v.get("status") in {"downloaded", "skipped_existing"}
                }
                indices = [i for i in indices if i not in completed]
                if not indices:
                    return {
                        "status": "already_completed",
                        "selection_token": selection_token,
                        "message": "All selected papers have already been downloaded.",
                        "total": len(original_indices),
                        "downloaded": len(completed),
                        "skipped_existing": 0,
                        "skipped": 0,
                        "failed": 0,
                        "results": list(download_state.get("results", {}).values()),
                    }

        # Initialize download state for checkpoint
        download_state = {
            "status": "in_progress",
            "results": {},
            "created_at": utc_now(),
            "download_strategy": download_strategy or "env_default",
            "use_libgen": use_libgen,
            "libgen_base_url": libgen_base_url,
        }
        await asyncio.to_thread(
            _cache_write_session_download_state, selection_token, download_state
        )

        parse_execution_name = _workflow_parse_execution_name(parse_execution)
        if _large_batch_confirmation_mismatch(session, indices):
            return {
                "status": "selection_mismatch",
                "selection_token": selection_token,
                "message": "Selected indices do not match the user-confirmed checkbox selection.",
                "confirmed_selected_indices": _confirmed_large_batch_indices(session),
                "requested_selected_indices": _format_selected_indices(indices),
                "downloaded": 0,
                "failed": 0,
            }
        _explicit = (
            _selected_indices_was_explicit(selected_indices)
            and str(selected_indices).strip().lower() not in {"all", "*"}
        )
        if _should_require_large_batch_selection(
            len(indices),
            large_batch_selection=large_batch_selection,
            bypass_large_batch_selection=False,
            session=session,
            public_call=True,
            explicit_user_selection=_explicit,
        ):
            selection_semantics = (
                SELECTION_SEMANTICS_DOWNLOAD_ONLY
                if parse_execution_name == "none"
                else SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE
            )
            prompt = await _pre_download_selection_prompt(
                selection_token=selection_token,
                session=session,
                indices=indices,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                parse_execution=parse_execution,
                custom_save_path_confirmed=custom_save_path_confirmed,
                selection_semantics=selection_semantics,
            )
            surface = _selection_surface_policy(force_open=True)
            response = {
                "status": "selection_required",
                "selection_token": prompt.get("selection_token", ""),
                "download_selection_token": selection_token,
                "query": session.get("query", ""),
                "sources": session.get("sources", ""),
                "selected_indices": indices,
                **save_path_meta,
                "use_scihub": use_scihub,
                "download_strategy": download_strategy or "env_default",
                "use_libgen": use_libgen,
                "parse_execution": parse_execution_name,
                "mode": mode or "auto",
                "backend": backend or "",
                "force": force,
                "total": len(indices),
                "downloaded": 0,
                "skipped_existing": 0,
                "skipped": 0,
                "failed": 0,
                "parse_prompt": _strip_widget_meta(prompt),
                "selection_semantics": selection_semantics,
                "large_batch_selection": _large_batch_selection_policy_name(
                    large_batch_selection
                ),
                "message": prompt.get("message", ""),
                "detected_host": surface.get("detected_host", "unknown"),
                "app_widget_supported": surface.get("app_widget_supported", False),
                "selection_surface": surface,
            }
            if isinstance(prompt.get("app"), dict):
                response["app"] = prompt["app"]
            if isinstance(prompt.get("local_browser"), dict):
                response["local_browser"] = prompt["local_browser"]
            return _to_widget_tool_result(
                _promote_paper_selection_app(
                _prefer_local_selection_surface(response)
            )
            )

        download_concurrency = (
            concurrency
            if concurrency and concurrency > 0
            else _env_int(DOWNLOAD_CONCURRENCY_ENV, 8, minimum=1)
        )
        semaphore = asyncio.Semaphore(download_concurrency)
        download_timeout = _env_float(DOWNLOAD_TIMEOUT_ENV, 30.0, minimum=1.0)

        async def _limited(
            index: int, shared_client: httpx.AsyncClient
        ) -> Dict[str, Any]:
            async with semaphore:
                paper = papers[index - 1]
                if not isinstance(paper, dict):
                    return {
                        "index": index,
                        "status": "skipped",
                        "message": "Stored search result is not a paper dictionary.",
                    }
                return await _download_selected_session_paper_wrapper(
                    paper=paper,
                    index=index,
                    save_path=save_path,
                    use_scihub=use_scihub,
                    client=shared_client,
                    download_strategy=download_strategy,
                    use_libgen=use_libgen,
                    libgen_base_url=libgen_base_url,
                    _searchers=searchers,
                )

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=download_timeout
        ) as shared_client:
            results = await asyncio.gather(
                *[_limited(index, shared_client) for index in indices]
            )

        # Persist download state for checkpoint/resume
        for result in results:
            idx = result.get("index")
            if idx is not None:
                download_state["results"][str(idx)] = {
                    "index": idx,
                    "status": result.get("status"),
                    "title": result.get("candidate", {}).get("title", ""),
                    "pdf_path": result.get("pdf_path", ""),
                    "download_method": result.get("download_method", ""),
                }
        download_state["status"] = "completed"
        await asyncio.to_thread(
            _cache_write_session_download_state, selection_token, download_state
        )

        downloaded = sum(
            1 for result in results if result.get("status") == "downloaded"
        )
        skipped_existing = sum(
            1 for result in results if result.get("status") == "skipped_existing"
        )
        skipped = sum(
            1 for result in results if result.get("status") == "skipped"
        )
        failed = len(results) - downloaded - skipped_existing - skipped
        status = (
            "ok"
            if failed == 0
            else "partial"
            if downloaded or skipped_existing
            else "failed"
        )
        successful_pdf_count = downloaded + skipped_existing
        parse_prompt: Dict[str, Any] = {}
        if successful_pdf_count > 0:
            parse_prompt = await _parse_prompt_for_download_results(
                selection_token=selection_token,
                session=session,
                results=results,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                parse_execution=parse_execution,
                ctx=ctx,
                custom_save_path_confirmed=custom_save_path_confirmed,
            )

        manifest_path = _download_manifest_path(save_path, selection_token)
        manifest = {
            "status": status,
            "selection_token": selection_token,
            "query": session.get("query", ""),
            "sources": session.get("sources", ""),
            "selected_indices": indices,
            **save_path_meta,
            "use_scihub": use_scihub,
            "download_strategy": download_strategy or "env_default",
            "use_libgen": use_libgen,
            "libgen_base_url": libgen_base_url,
            "download_concurrency": download_concurrency,
            "parse_execution": parse_execution_name,
            "mode": mode or "auto",
            "backend": backend or "",
            "force": force,
            "created_at": utc_now(),
            "results": results,
            "total": len(results),
            "downloaded": downloaded,
            "skipped_existing": skipped_existing,
            "successful_pdf_count": successful_pdf_count,
            "skipped": skipped,
            "failed": failed,
            "parse_prompt": parse_prompt,
        }
        await asyncio.to_thread(write_json, manifest_path, manifest)

        response = {**manifest, "manifest_path": manifest_path}
        if isinstance(parse_prompt, dict) and isinstance(
            parse_prompt.get("app"), dict
        ):
            response["app"] = parse_prompt["app"]
        return _to_widget_tool_result(
            _promote_paper_selection_app(response)
            if _should_promote_paper_selection_app(parse_prompt)
            else response
        )

    # =======================================================================
    #  resume_download
    # =======================================================================
    @mcp.tool()
    async def resume_download(
        selection_token: str,
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        concurrency: int = 0,
        parse_execution: str = "none",
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
        custom_save_path_confirmed: bool = False,
        ctx: Optional[Context] = None,
    ) -> Dict[str, Any]:
        """Resume a previous download session, skipping already-downloaded papers.

        Call this after an interrupted download to continue where you left off.
        Papers already saved to disk are detected and skipped automatically.
        """
        return await download_selected_papers(
            selection_token=selection_token,
            selected_indices="all",
            save_path=save_path,
            use_scihub=use_scihub,
            concurrency=concurrency,
            parse_execution=parse_execution,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            resume=True,
            ctx=ctx,
        )

    # =======================================================================
    #  crawl_download_parse_papers
    # =======================================================================
    @mcp.tool()
    async def crawl_download_parse_papers(
        query: str,
        count: int = 10,
        max_results_per_source: int = 5,
        sources: str = "",
        year: Optional[str] = None,
        ranking_profile: str = "",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        download_concurrency: int = 0,
        parse_execution: str = "none",
        parse_mode: str = "auto",
        backend: str = "",
        force: bool = False,
        custom_save_path_confirmed: bool = False,
        large_batch_selection: str = "auto",
        bypass_large_batch_selection: bool = False,
        ctx: Optional[Context] = None,
    ) -> Dict[str, Any]:
        """Compatibility workflow: search, download top-ranked papers, then apply the parse policy.

        For natural-language MCP use, prefer paper_research_workflow. It can also
        submit the background parse job so the caller does not need to interpret
        parse_prompt manually.

        When more than 10 papers are requested, a real checkbox UI confirmation
        is required.  Programmatic bypass is not available through this public entry point.
        """
        invalid_save_path = _invalid_mcp_save_path(
            save_path,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        if invalid_save_path:
            return invalid_save_path

        limit = max(1, int(count or 1))
        selection = await crawl_papers_for_selection(
            query=query,
            max_results_per_source=max_results_per_source,
            sources=sources,
            year=year,
            ranking_profile=ranking_profile,
            requested_count=limit,
        )
        # ── Unwrap ToolResult from crawl_papers_for_selection ──────────────
        selection, _ = _unwrap_tool_result(selection)
        if not isinstance(selection, dict):
            selection = {}
        papers = selection.get("papers", [])
        if not isinstance(papers, list) or not papers:
            return {
                "status": "no_results",
                "query": query,
                "selection": selection,
                "message": "No papers were found to download.",
            }

        selected_indices = ",".join(
            str(index) for index in range(1, min(limit, len(papers)) + 1)
        )
        parse_execution_name = _workflow_parse_execution_name(parse_execution)
        parsed_indices = (
            _parse_selected_indices(selected_indices, len(papers))
            if selected_indices
            else []
        )
        should_apply_parse_policy = parse_execution_name != "none"
        if _should_require_large_batch_selection(
            len(parsed_indices),
            large_batch_selection=large_batch_selection,
            bypass_large_batch_selection=False,
            public_call=True,
        ) or limit > AUTO_PARSE_SAVED_PDF_LIMIT:
            selection_semantics = (
                SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE
                if should_apply_parse_policy
                else SELECTION_SEMANTICS_DOWNLOAD_ONLY
            )
            selection_parse_execution = (
                parse_execution if should_apply_parse_policy else "none"
            )
            prompt = await _pre_download_selection_prompt(
                selection_token=selection["selection_token"],
                session={
                    "query": query,
                    "sources": (
                        selection.get("sources_requested", sources)
                        if isinstance(selection, dict)
                        else sources
                    ),
                    "papers": papers,
                },
                indices=parsed_indices,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=parse_mode,
                backend=backend,
                force=force,
                parse_execution=selection_parse_execution,
                custom_save_path_confirmed=custom_save_path_confirmed,
                selection_semantics=selection_semantics,
            )
            response = {
                "status": "selection_required",
                "query": query,
                "count_requested": limit,
                "selected_indices": selected_indices,
                "save_path": resolve_save_path(save_path),
                "selection": _strip_widget_meta(selection),
                "download": None,
                "parse_prompt": _strip_widget_meta(prompt),
                "parse_decision_required": True,
                "requires_user_parse_decision": True,
                "selection_semantics": selection_semantics,
                "large_batch_selection": _large_batch_selection_policy_name(
                    large_batch_selection
                ),
                "message": prompt.get("message", ""),
            }
            if isinstance(prompt.get("app"), dict):
                response["app"] = prompt["app"]
            if isinstance(prompt.get("local_browser"), dict):
                response["local_browser"] = prompt["local_browser"]
            return _to_widget_tool_result(
                _promote_paper_selection_app(
                _prefer_local_selection_surface(response)
            )
            )

        download = await download_selected_papers(
            selection_token=selection["selection_token"],
            selected_indices=selected_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            concurrency=download_concurrency,
            parse_execution=(
                "prompt" if limit > AUTO_PARSE_SAVED_PDF_LIMIT else parse_execution
            ),
            mode=parse_mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            large_batch_selection=large_batch_selection,
            bypass_large_batch_selection=False,
            ctx=ctx,
        )
        # ── Unwrap ToolResult from download_selected_papers ──────────────────
        download, _ = _unwrap_tool_result(download)
        if not isinstance(download, dict):
            download = {}
        status = (
            download.get("status", "unknown")
            if isinstance(download, dict)
            else "unknown"
        )
        response = {
            "status": status,
            "query": query,
            "count_requested": limit,
            "selected_indices": selected_indices,
            "save_path": resolve_save_path(save_path),
            "selection": selection,
            "download": download,
            "parse_prompt": download.get("parse_prompt")
            if isinstance(download, dict)
            else None,
        }
        if isinstance(download, dict) and isinstance(download.get("app"), dict):
            response["app"] = download["app"]
            if _should_promote_paper_selection_app(download):
                _promote_paper_selection_app(response)
        return _to_widget_tool_result(response)

    # =======================================================================
    #  paper_research_workflow
    # =======================================================================
    @mcp.tool()
    async def paper_research_workflow(
        query: str,
        intent: str = "search_download_parse",
        count: int = 5,
        max_results_per_source: int = 5,
        sources: str = "",
        year: Optional[str] = None,
        ranking_profile: str = "",
        selection_mode: str = "auto_top",
        selected_indices: str = "",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        parse_mode: str = "auto",
        backend: str = "",
        force: bool = False,
        parse_execution: str = "prompt",
        download_concurrency: int = 0,
        custom_save_path_confirmed: bool = False,
        large_batch_selection: str = "auto",
        bypass_large_batch_selection: bool = False,
        ctx: Optional[Context] = None,
    ) -> Dict[str, Any]:
        """Preferred MCP-first natural-language workflow for paper research.

        Use this high-level tool when the user asks in natural language to find,
        select, download, or parse papers. It coordinates the lower-level MCP tools
        directly and only returns CLI instructions as a fallback for unavailable
        host capabilities. By default, it downloads selected PDFs and asks before
        MinerU parsing; set parse_execution="none" to skip the parse prompt.

        When more than 10 papers are requested, a real checkbox UI confirmation
        is required.  Programmatic bypass is not available through this public entry point.
        """
        intent_name = _workflow_intent_name(intent)
        selection_mode_name = _workflow_intent_name(selection_mode)
        parse_execution_name = _workflow_parse_execution_name(parse_execution)

        # ── Auto-detect ranking profile from query text ────────────────
        #     When no explicit ranking_profile is given, classify the query
        #     against all registered profiles.  The top match (if confidence
        #     ≥ 2.0) is auto-selected.  Falls back to agent-skill detection
        #     for backward compatibility when the classifier is uncertain.
        if not ranking_profile:
            intents = _classify_query_intent(query, top_k=1)
            if intents and intents[0][1] >= 2.0:
                ranking_profile = intents[0][0]
            else:
                # Legacy fallback: check agent-skill vocabulary
                query_doc = {"title": query, "abstract": ""}
                if _agent_skill_profile_score(query_doc) >= 0.5:
                    ranking_profile = AGENT_SKILL_RANKING_PROFILE

        search_only_intents = {
            "search",
            "search_only",
            "find",
            "find_only",
            "discover",
            "selection",
            "select",
        }
        manual_selection = selection_mode_name in {
            "manual",
            "choose",
            "selection",
            "select",
        }
        should_download = (
            intent_name not in search_only_intents and not manual_selection
        )
        parse_disabled_intents = {
            "download_only",
            "search_download_only",
            "no_parse",
            "without_parse",
            "skip_parse",
            "download_no_parse",
        }
        should_apply_parse_policy = (
            should_download
            and parse_execution_name != "none"
            and intent_name not in parse_disabled_intents
            and _intent_explicitly_requests_parse(intent_name)
        )

        selection = await crawl_papers_for_selection(
            query=query,
            max_results_per_source=max_results_per_source,
            sources=sources,
            year=year,
            ranking_profile=ranking_profile,
            requested_count=count,
        )
        # ── Unwrap FastMCP ToolResult from crawl_papers_for_selection ──────
        # When called internally, crawl_papers_for_selection returns a
        # ToolResult.  Unwrap to a plain dict so downstream code can read
        # .get("papers"), .get("selection_token"), etc.
        selection, _selection_widget_meta = _unwrap_tool_result(selection)
        papers = selection.get("papers", []) if isinstance(selection, dict) else []
        if not isinstance(papers, list) or not papers:
            return {
                "status": "no_results",
                "workflow": {
                    "tool": "paper_research_workflow",
                    "mcp_first": True,
                    "intent": intent_name,
                    "selection_mode": selection_mode_name,
                },
                "query": query,
                "selection": selection,
                "message": "No papers were found for the query.",
            }

        if not should_download:
            response = {
                "status": "selection_ready",
                "workflow": {
                    "tool": "paper_research_workflow",
                    "mcp_first": True,
                    "intent": intent_name,
                    "selection_mode": selection_mode_name,
                    "next_tool": PAPER_SELECTION_WIDGET_TOOL,
                },
                "query": query,
                "selection": selection,
                "recommended_tool": PAPER_SELECTION_WIDGET_TOOL,
                "recommended_selected_indices": "",
                "message": (
                    "Paper candidates are ready. Present the checkbox UI "
                    "or numbered list to the user so they can choose which "
                    "papers to download. Do NOT call download_selected_papers "
                    "directly — the user must select papers first."
                ),
            }
            if isinstance(selection, dict) and isinstance(
                selection.get("app"), dict
            ):
                response["app"] = selection["app"]
            # ── Fallback: restore _meta from the crawl_papers_for_selection
            #     ToolResult when the unwrapped app dict did not carry one.
            if (
                _selection_widget_meta
                and isinstance(response.get("app"), dict)
                and not isinstance(response["app"].get("_meta"), dict)
            ):
                response["app"]["_meta"] = _selection_widget_meta
            return _to_widget_tool_result(_promote_paper_selection_app(response))

        indices = _workflow_selection_indices(
            selected_indices,
            selection_mode,
            count,
            len(papers),
            papers=papers,
        )
        if not indices:
            return {
                "status": "invalid_selection",
                "workflow": {
                    "tool": "paper_research_workflow",
                    "mcp_first": True,
                    "intent": intent_name,
                    "selection_mode": selection_mode_name,
                },
                "query": query,
                "selection": selection,
                "message": "No selected papers were available to download.",
            }

        try:
            parsed_workflow_indices = _parse_selected_indices(
                indices, len(papers)
            )
        except ValueError as exc:
            return {
                "status": "invalid_selection",
                "workflow": {
                    "tool": "paper_research_workflow",
                    "mcp_first": True,
                    "intent": intent_name,
                    "selection_mode": selection_mode_name,
                },
                "query": query,
                "selection": selection,
                "message": str(exc),
            }

        if _should_require_large_batch_selection(
            len(parsed_workflow_indices),
            large_batch_selection=large_batch_selection,
            bypass_large_batch_selection=False,
            public_call=True,
        ) or count > AUTO_PARSE_SAVED_PDF_LIMIT:
            selection_semantics = (
                SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE
                if should_apply_parse_policy
                else SELECTION_SEMANTICS_DOWNLOAD_ONLY
            )
            # 若 crawl_papers_for_selection 已创建本地浏览器页面，跳过重复创建
            _skip_ui = bool(
                isinstance(selection, dict)
                and isinstance(selection.get("local_browser"), dict)
                and selection["local_browser"].get("url")
            )
            prompt = await _pre_download_selection_prompt(
                selection_token=selection["selection_token"],
                session={
                    "query": query,
                    "sources": (
                        selection.get("sources_requested", sources)
                        if isinstance(selection, dict)
                        else sources
                    ),
                    "papers": papers,
                },
                indices=parsed_workflow_indices,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=parse_mode,
                backend=backend,
                force=force,
                parse_execution=(parse_execution if should_apply_parse_policy else "none"),
                custom_save_path_confirmed=custom_save_path_confirmed,
                selection_semantics=selection_semantics,
                skip_local_ui=_skip_ui,
            )
            response: Dict[str, Any] = {
                "status": "selection_required",
                "workflow": {
                    "tool": "paper_research_workflow",
                    "mcp_first": True,
                    "intent": intent_name,
                    "selection_mode": selection_mode_name,
                    "selected_indices": indices,
                    "parse_execution": (
                        parse_execution_name if should_apply_parse_policy else "none"
                    ),
                    "next_tool": PAPER_SELECTION_WIDGET_TOOL,
                },
                "query": query,
                "count_requested": count,
                "selected_indices": indices,
                **_mcp_save_path_metadata(
                    save_path,
                    custom_save_path_confirmed=custom_save_path_confirmed,
                ),
                "download_selection_token": selection.get("selection_token", ""),
                "display_total": len(prompt.get("papers", [])) if isinstance(prompt, dict) else 0,
                "full_total": len(papers),
                "parse_execution": (
                    parse_execution_name if should_apply_parse_policy else "none"
                ),
                "selection_semantics": selection_semantics,
                "large_batch_selection": _large_batch_selection_policy_name(
                    large_batch_selection
                ),
                "message": prompt.get("message", ""),
            }
            if isinstance(prompt.get("app"), dict):
                response["app"] = prompt["app"]
            if isinstance(prompt.get("local_browser"), dict):
                response["local_browser"] = prompt["local_browser"]
            # 复用 crawl_papers_for_selection 已创建的本地浏览器页面
            existing_lb = (
                selection.get("local_browser")
                if isinstance(selection, dict)
                else None
            )
            if isinstance(existing_lb, dict) and existing_lb.get("url"):
                response["local_browser"] = existing_lb
                response["local_browser_url"] = existing_lb.get("url")
            return _to_widget_tool_result(
                _promote_paper_selection_app(
                    _prefer_local_selection_surface(response)
                )
            )

        download = await download_selected_papers(
            selection_token=selection["selection_token"],
            selected_indices=indices,
            save_path=save_path,
            use_scihub=use_scihub,
            concurrency=download_concurrency,
            parse_execution=(
                "prompt" if count > AUTO_PARSE_SAVED_PDF_LIMIT
                else (parse_execution if should_apply_parse_policy else "none")
            ),
            mode=parse_mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            large_batch_selection=large_batch_selection,
            bypass_large_batch_selection=False,
            ctx=ctx,
        )
        # ── Unwrap FastMCP ToolResult from download_selected_papers ──────────
        # download_selected_papers may return a ToolResult when its own
        # large-batch gate triggers (pre-download checkbox selection).
        download, _download_widget_meta = _unwrap_tool_result(download)
        if not isinstance(download, dict):
            download = {}

        response = {
            "status": (
                download.get("status", "unknown")
                if isinstance(download, dict)
                else "unknown"
            ),
            "workflow": {
                "tool": "paper_research_workflow",
                "mcp_first": True,
                "intent": intent_name,
                "selection_mode": selection_mode_name,
                "selected_indices": indices,
                "parse_execution": (
                    parse_execution_name if should_apply_parse_policy else "none"
                ),
            },
            "query": query,
            "count_requested": count,
            "selected_indices": indices,
            **_mcp_save_path_metadata(
                save_path, custom_save_path_confirmed=custom_save_path_confirmed
            ),
            "selection": selection,
            "download": download,
            "parse_prompt": (
                download.get("parse_prompt")
                if isinstance(download, dict)
                else None
            ),
        }
        if isinstance(download, dict) and isinstance(download.get("app"), dict):
            response["app"] = download["app"]
            if _should_promote_paper_selection_app(download):
                _promote_paper_selection_app(response)

        parse_prompt = response.get("parse_prompt")
        if not should_apply_parse_policy or parse_execution_name == "none":
            response["workflow"]["next_tool"] = ""
            return _to_widget_tool_result(response)

        if isinstance(parse_prompt, dict) and isinstance(
            parse_prompt.get("parse_job"), dict
        ):
            response["parse_job"] = parse_prompt["parse_job"]
            response["status"] = parse_prompt["parse_job"].get(
                "status", response["status"]
            )
            response["workflow"]["next_tool"] = "get_parse_job_status"
            return response

        if (
            isinstance(parse_prompt, dict)
            and parse_prompt.get("interaction") == "auto_parse_saved_pdfs"
        ):
            response["parse"] = parse_prompt
            response["status"] = parse_prompt.get("status", response["status"])
            response["workflow"]["next_tool"] = "get_parsed_paper"
            return response

        if (
            isinstance(parse_prompt, dict)
            and parse_prompt.get("recommended_tool") == PAPER_SELECTION_WIDGET_TOOL
        ):
            response["workflow"]["next_tool"] = PAPER_SELECTION_WIDGET_TOOL
            response["parse_decision_required"] = True
            response["requires_user_parse_decision"] = True
            return response

        if not isinstance(parse_prompt, dict) or not parse_prompt.get(
            "selection_token"
        ):
            response["status"] = "partial"
            response["message"] = (
                "Download finished, but no parse-ready PDFs were found."
            )
            return response

        parse_ready_total = int(parse_prompt.get("parse_ready_total") or 0)
        if parse_ready_total <= 0:
            response["status"] = "partial"
            response["message"] = (
                "Download finished, but no parse-ready PDFs were found."
            )
            return response

        parse_selection_token = str(parse_prompt["selection_token"])
        parse_indices = str(
            parse_prompt.get("recommended_selected_indices") or "all"
        )

        # Lazy-import cross-tool calls from .core
        from .core import (  # noqa: PLC0415
            _run_parse_selected_papers as parse_selected_papers,
            _run_submit_parse_job as submit_parse_job,
        )

        if parse_execution_name == "sync":
            parse_result = await parse_selected_papers(
                selection_token=parse_selection_token,
                selected_indices=parse_indices,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=parse_mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
            )
            response["parse"] = parse_result
            response["status"] = (
                parse_result.get("status", response["status"])
                if isinstance(parse_result, dict)
                else response["status"]
            )
            response["workflow"]["next_tool"] = "get_parsed_paper"
            return response

        parse_job = await submit_parse_job(
            parse_fn=parse_selected_papers,
            selection_token=parse_selection_token,
            selected_indices=parse_indices,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=parse_mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        response["parse_job"] = parse_job
        response["status"] = (
            parse_job.get("status", response["status"])
            if isinstance(parse_job, dict)
            else response["status"]
        )
        response["workflow"]["next_tool"] = "get_parse_job_status"
        return response

    # ---- Export closures to module level for direct callers (CLI, tests) ----
    _set_module_functions(
        search_papers=search_papers,
        search_papers_for_parsing=search_papers_for_parsing,
        crawl_papers_for_selection=crawl_papers_for_selection,
        search_papers_with_elicitation=search_papers_with_elicitation,
        download_with_fallback=download_with_fallback,
        download_selected_papers=download_selected_papers,
        resume_download=resume_download,
        crawl_download_parse_papers=crawl_download_parse_papers,
        paper_research_workflow=paper_research_workflow,
    )
