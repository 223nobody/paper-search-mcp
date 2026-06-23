# paper_search_mcp/server.py
from typing import List, Dict, Optional, Any
import asyncio
import contextvars
import copy
import json
import os
import logging
import queue
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from pathlib import Path
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, create_model
from .config import env_file_path, get_env, set_env_value
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.pmc import PMCSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.europepmc import EuropePMCSearcher
from .academic_platforms.sci_hub import SciHubFetcher
from .academic_platforms.dblp import DBLPSearcher
from .academic_platforms.openaire import OpenAiresearcher
from .academic_platforms.citeseerx import CiteSeerXSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.base_search import BASESearcher
from .academic_platforms.unpaywall import UnpaywallResolver, UnpaywallSearcher
from .academic_platforms.zenodo import ZenodoSearcher
from .academic_platforms.hal import HALSearcher
from .academic_platforms.ssrn import SSRNSearcher
from .utils import (
    DEFAULT_SAVE_PATH,
    detect_host,
    extract_doi,
    host_supports_mcp_apps_widget,
    open_url_in_host,
    resolve_save_path,
)
from .selection_confirmation import (
    confirmation_required_response,
    consume_selection_confirmation_token,
    create_selection_confirmation_token,
    format_selected_indices as _format_selection_indices,
    normalize_selected_indices,
    selection_revision as _selection_confirmation_revision,
)
from .cache import (
    cleanup_redundant_artifacts,
    cleanup_stale_cache_entries as cache_cleanup_stale_cache_entries,
    create_search_session as cache_create_search_session,
    delete_cache,
    delete_search_session as cache_delete_search_session,
    delete_session_download_state as cache_delete_session_download_state,
    get_download_health,
    get_search_session as cache_get_search_session,
    read_selection_ui_state as cache_read_selection_ui_state,
    read_session_download_state as cache_read_session_download_state,
    write_session_download_state as cache_write_session_download_state,
    find_download_by_pdf_path,
    index_parsed_paper,
    list_assets,
    list_parsed,
    list_parse_job_records,
    list_search_sessions as cache_list_search_sessions,
    rank_download_methods,
    read_parsed,
    read_parse_job,
    rebuild_parsed_index,
    record_download,
    record_download_health,
    resolved_parsed_paths,
    search_parsed,
    search_parsed_index,
    sha256_file,
    utc_now,
    write_json,
    write_parse_job,
    _session_path as cache_session_path,
)
from .parsers.mineru import (
    mineru_health_check as run_mineru_health_check,
    parse_pdf_with_mineru as run_parse_pdf_with_mineru,
    parse_pdfs_with_mineru as run_parse_pdfs_with_mineru,
)

# from .academic_platforms.hub import SciHubSearcher
from .paper import Paper

# ===========================================================================
# Engine module imports
# ===========================================================================
from .engine.paper import (
    _paper_unique_key, _paper_score, _paper_doi, _paper_field, _paper_value,
    _paper_parse_candidate, _paper_year, _paper_year_number,
    _paper_original_url, _paper_publication_date, _paper_publication_venue,
    _paper_has_pdf_signal, _paper_citations, _normalize_lookup_text,
    _paper_extra_value, _paper_profile_text,
    _agent_skill_profile_score, _ranking_profile_name, _rank_papers_for_profile,
    _dedupe_papers, _merge_paper_record, _normalize_identifier_doi,
    _extract_arxiv_id, _canonical_pdf_stem, _safe_filename, _pdf_filename_from_hint,
    _looks_like_pdf_path, _token_set, _title_similarity,
    _repository_paper_matches_request, _is_generic_publication_venue,
    _arxiv_category_venue, _searcher_for_source, _source_from_identifier,
    _env_bool, PREFER_ARXIV_ENV,
    AGENT_SKILL_RANKING_PROFILE, AGENT_SKILL_PROFILE_ALIASES,
    AGENT_SKILL_BOOST_PHRASES, AGENT_SKILL_AGENT_TERMS, AGENT_SKILL_SKILL_TERMS,
    AGENT_SKILL_NEGATIVE_PHRASES,
    GENERIC_PUBLICATION_VENUES, ARXIV_CATEGORY_VENUES, ARXIV_ID_RE, ARXIV_DOI_RE,
)
from .engine.search import (
    async_search, _parse_sources, _disabled_sources,
    _source_capability_report, _source_config_status,
    _source_reliability, _rank_sources_by_reliability,
    _search_source_with_timeout, _search_cache_key,
    _cached_search_result, _store_search_result,
    _env_int, _env_float, _split_env_csv,
    ALL_SOURCES, FAST_SOURCES, PDF_CS_SOURCES, DEEP_SOURCES,
    AGENT_SKILL_FAST_SOURCES, AGENT_SKILL_BROAD_SOURCES,
    SEARCH_PROFILES, SOURCE_CAPABILITIES, SOURCE_RELIABILITY_SCORES, SOURCE_CONFIG_KEYS,
    SEARCH_PROFILE_ENV, SEARCH_CACHE_TTL_ENV, DISABLED_SOURCES_ENV, SEARCH_RESULT_CACHE,
)
from .engine.download import (
    _download_source_pdf as _engine_download_source_pdf,
    _read_source_paper as _engine_read_source_paper,
    _download_with_fallback_path as _engine_download_with_fallback_path,
    _download_from_url, _race_oa_downloads,
    _download_selected_session_paper as _engine_download_selected_session_paper,
    _resolve_session_paper_pdf as _engine_resolve_session_paper_pdf,
    _is_valid_pdf_file, _pdf_result_metadata,
    _candidate_download_id, _existing_pdf_candidates, _find_existing_pdf,
    _invalid_mcp_save_path, _wrap_save_path_methods,
    _custom_save_paths_allowed, _explicit_save_path_required,
    _download_manifest_path, _env_flag_enabled, _mcp_save_path_metadata,
    _download_strategy as _engine_download_strategy,
    _libgen_enabled as _engine_libgen_enabled,
    _after_saved_pdf as _engine_after_saved_pdf,
    _after_saved_pdfs as _engine_after_saved_pdfs,
    _saved_pdf_batch_window_seconds, _saved_pdf_batch_prompt_enabled,
    _snapshot_pdf_files, _changed_pdf_paths, _recent_saved_pdf_papers,
    _downloaded_pdf_paper, _paper_from_download_metadata, _downloaded_pdf_papers,
    _pdf_path_from_result, _pdf_paths_from_result,
    _try_paper_fetch_download as _engine_try_paper_fetch_download,
    DOWNLOAD_TIMEOUT_ENV, DOWNLOAD_MAX_RETRIES_ENV, DOWNLOAD_RETRY_BACKOFF_ENV,
)
from .engine.parse import (
    _parse_selected_indices, _parse_elicitation_selected_indices,
    _workflow_parse_execution_name, _selection_semantics_name,
    _prompt_parse_saved_pdfs as _engine_prompt_parse_saved_pdfs,
    _parse_prompt_for_download_results as _engine_parse_prompt_for_download_results,
    _pre_download_selection_prompt as _engine_pre_download_selection_prompt,
    _numbered_paper_fallback,
    _paper_selection_app_meta, _paper_selection_tool_meta,
    _paper_selection_app_payload, _paper_selection_app_prompt,
    _codex_app_display_candidates, _codex_recommended_selected_indices,
    _promote_paper_selection_app, _should_promote_paper_selection_app,
    _strip_widget_meta,
    _mineru_api_key_configured, _mineru_batch_parse_enabled,
    _mineru_key_app_meta, _mineru_key_setup_prompt,
    _is_mineru_api_key_error, _mineru_api_key_prompt_for_parse_result,
    _attach_mineru_key_prompt, _first_mineru_key_prompt,
    dismiss_parse_prompt_state,
    _auto_open_selection_ui_enabled, _selection_ui_mode, _selection_ui_should_open,
    _selection_surface_policy,
    _shorten_for_option, _elicitation_option_label,
    _build_paper_selection_schema, _create_paper_selection_result, _arxiv_metadata_for_id,
    AUTO_PARSE_SAVED_PDF_LIMIT,
    SELECTION_SEMANTICS_PARSE, SELECTION_SEMANTICS_DOWNLOAD_ONLY,
    SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
    PAPER_SELECTION_WIDGET_URI, PAPER_SELECTION_WIDGET_TOOL,
    MINERU_KEY_WIDGET_URI, MINERU_KEY_WIDGET_TOOL,
    SAVED_PDF_BATCH_PROMPT_ENV, SAVED_PDF_BATCH_WINDOW_ENV,
    AUTO_OPEN_SELECTION_UI_ENV, SELECTION_UI_MODE_ENV,
)
from .engine.jobs import (
    _parse_job_stage_progress, _parse_job_result_status,
    _parse_job_item_from_candidate, _refresh_parse_job_progress,
    _update_parse_job_item, _update_parse_job_items, _parse_job_preview_items,
    _parse_job_snapshot, _serializable_parse_job,
    _persist_parse_job, _update_parse_job,
    _run_parse_job, _run_parse_job_thread,
    _PARSE_JOBS, _PARSE_JOB_LOCK, _CURRENT_PARSE_JOB_ID,
    _PARSE_ITEM_TERMINAL_STATUSES,
    progress_subscribe, progress_unsubscribe,
)
from .widgets.response import widget_tool_result


# Initialize MCP server
mcp = FastMCP("paper_search_server")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Idle timeout: exit the process after 3 minutes of inactivity so that
# orphaned stdio processes do not accumulate indefinitely.
# ---------------------------------------------------------------------------
_IDLE_TIMEOUT_SECONDS = 3 * 60  # 3 minutes
_last_activity_time = time.time()
_ACTIVITY_LOCK = threading.Lock()


def _update_activity():
    """Mark the most recent tool-call or UI-interaction timestamp."""
    global _last_activity_time
    with _ACTIVITY_LOCK:
        _last_activity_time = time.time()


def _idle_timeout_monitor():
    """Background daemon: exit the process after _IDLE_TIMEOUT_SECONDS of inactivity."""
    while True:
        time.sleep(60)  # check once per minute
        with _ACTIVITY_LOCK:
            idle_seconds = time.time() - _last_activity_time
        if idle_seconds > _IDLE_TIMEOUT_SECONDS:
            logger.info(
                "Idle for %.0f s (> %d s); shutting down.",
                idle_seconds,
                _IDLE_TIMEOUT_SECONDS,
            )
            os._exit(0)
ALLOW_CUSTOM_SAVE_PATH_ENV = "ALLOW_CUSTOM_SAVE_PATH"
REQUIRE_EXPLICIT_SAVE_PATH_ENV = "REQUIRE_EXPLICIT_SAVE_PATH"
SEARCH_PROFILE_ENV = "SEARCH_PROFILE"
SEARCH_TIMEOUT_ENV = "SEARCH_TIMEOUT_SECONDS"
SEARCH_SOURCE_TIMEOUT_ENV = "SEARCH_SOURCE_TIMEOUT_SECONDS"
SEARCH_CACHE_TTL_ENV = "SEARCH_CACHE_TTL_SECONDS"
DOWNLOAD_TIMEOUT_ENV = "DOWNLOAD_TIMEOUT_SECONDS"
DOWNLOAD_MAX_RETRIES_ENV = "DOWNLOAD_MAX_RETRIES"
DOWNLOAD_RETRY_BACKOFF_ENV = "DOWNLOAD_RETRY_BACKOFF_SECONDS"
PREFER_ARXIV_ENV = "PREFER_ARXIV"
PARSE_CONCURRENCY_ENV = "PARSE_CONCURRENCY"
DOWNLOAD_CONCURRENCY_ENV = "DOWNLOAD_CONCURRENCY"
DISABLED_SOURCES_ENV = "DISABLED_SOURCES"
AUTO_OPEN_SELECTION_UI_ENV = "AUTO_OPEN_SELECTION_UI"
SELECTION_UI_MODE_ENV = "SELECTION_UI_MODE"
SAVED_PDF_BATCH_PROMPT_ENV = "SAVED_PDF_BATCH_PROMPT"
SAVED_PDF_BATCH_WINDOW_ENV = "SAVED_PDF_BATCH_WINDOW_SECONDS"
PAPER_SELECTION_WIDGET_URI = "ui://paper-search/paper-selection.html"
PAPER_SELECTION_WIDGET_TOOL = "render_paper_selection_app"
MINERU_KEY_WIDGET_URI = "ui://paper-search/mineru-api-key.html"
MINERU_KEY_WIDGET_TOOL = "render_mineru_api_key_setup_app"
MINERU_KEY_CONFIG_TOOL = "configure_mineru_api_key"
LOCAL_PAPER_SELECTION_TOOL = "open_paper_selection_page"
LOCAL_PAPER_SELECTION_PATH = "/paper-selection"
DOWNLOAD_SELECTION_TIMEOUT_SECONDS_ENV = "DOWNLOAD_SELECTION_TIMEOUT_SECONDS"
PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS_ENV = "PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS"
DEFAULT_DOWNLOAD_SELECTION_TIMEOUT_SECONDS = 180
AUTO_PARSE_SAVED_PDF_LIMIT = 10
LARGE_BATCH_SELECTION_ENV = "LARGE_BATCH_SELECTION"
SELECTION_SEMANTICS_PARSE = "parse_selected"
SELECTION_SEMANTICS_DOWNLOAD_ONLY = "download_selected_only"
SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE = "download_and_parse_selected_only"
_LOCAL_SELECTION_LOCK = threading.Lock()
_LOCAL_SELECTION_SERVER: Optional[ThreadingHTTPServer] = None
_LOCAL_SELECTION_THREAD: Optional[threading.Thread] = None
_LOCAL_SELECTION_BASE_URL = ""
_LOCAL_SELECTION_PAGES: Dict[str, Dict[str, Any]] = {}
_SEARCH_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}


def _large_batch_selection_policy_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    if normalized in {"never", "none", "off", "false", "no", "disable", "disabled", "bypass"}:
        return "never"
    if normalized in {"always", "prompt", "manual", "select", "selection", "checkbox", "ask"}:
        return "always"
    if normalized in {"auto", ""}:
        return "auto"
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


def _format_selected_indices(indices: List[int]) -> str:
    return ",".join(str(index) for index in indices)


def _selection_revision(session: Dict[str, Any]) -> str:
    papers = session.get("papers", []) if isinstance(session, dict) else []
    total = len(papers) if isinstance(papers, list) else 0
    return str(session.get("updated_at") or session.get("created_at") or total)


def _selection_state_payload(
    selection_token: str,
    session: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    selected = state.get("selected_indices", []) if isinstance(state, dict) else []
    if not isinstance(selected, list):
        selected = []
    normalized: List[int] = []
    for value in selected:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    metadata = session.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    confirmed_arg = str(metadata.get("confirmed_selected_indices") or "").strip()
    confirmed_indices: List[int] = []
    if confirmed_arg:
        papers = session.get("papers", [])
        total = len(papers) if isinstance(papers, list) else 0
        try:
            confirmed_indices = _parse_selected_indices(confirmed_arg, total)
        except ValueError:
            confirmed_indices = []
    effective_indices = confirmed_indices or normalized
    revision = _selection_revision(session)
    return {
        "selection_token": selection_token,
        "selected_indices": effective_indices,
        "selected_indices_arg": _format_selected_indices(effective_indices),
        "draft_selected_indices": normalized,
        "draft_selected_indices_arg": _format_selected_indices(normalized),
        "has_saved_state": isinstance(state, dict)
        and "selected_indices" in state,
        "confirmed_selected_indices": confirmed_arg,
        "large_batch_selection_satisfied": bool(
            metadata.get("large_batch_selection_satisfied")
        ),
        "selection_revision": revision,
        "state_revision": str(state.get("selection_revision") or "")
        if isinstance(state, dict)
        else "",
        "submitted": bool(state.get("submitted")) if isinstance(state, dict) else False,
        "updated_at": state.get("updated_at", "") if isinstance(state, dict) else "",
    }


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
    return item_count > AUTO_PARSE_SAVED_PDF_LIMIT


def _configure_http_transport_from_env() -> None:
    host = get_env("HOST", mcp.settings.host).strip() or mcp.settings.host
    port_raw = get_env("PORT", str(mcp.settings.port)).strip()
    try:
        port = int(port_raw)
    except ValueError:
        logger.warning("Invalid PAPER_SEARCH_MCP_PORT=%r; using %s", port_raw, mcp.settings.port)
        port = mcp.settings.port

    path = get_env("MCP_PATH", mcp.settings.streamable_http_path).strip() or mcp.settings.streamable_http_path
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path

    disable_security = _env_bool("DISABLE_DNS_REBINDING_PROTECTION", False)
    allowed_hosts = _split_env_csv(get_env("ALLOWED_HOSTS", ""))
    allowed_origins = _split_env_csv(get_env("ALLOWED_ORIGINS", ""))
    if disable_security:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=[],
            allowed_origins=[],
        )
        return

    if allowed_hosts or allowed_origins:
        existing = mcp.settings.transport_security or TransportSecuritySettings()
        mcp.settings.transport_security = existing.model_copy(
            update={
                "allowed_hosts": allowed_hosts or existing.allowed_hosts,
                "allowed_origins": allowed_origins or existing.allowed_origins,
            }
        )


def _local_selection_page_url(page_id: str) -> str:
    _ensure_local_selection_server()
    return f"{_LOCAL_SELECTION_BASE_URL}{LOCAL_PAPER_SELECTION_PATH}/{page_id}"


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
        SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
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
    parse_execution: str = "none",
) -> Dict[str, Any]:
    page_id = secrets.token_urlsafe(16)
    confirmation_token = secrets.token_urlsafe(24)
    semantics = _selection_semantics_name(selection_semantics)
    timeout_seconds = _download_selection_timeout_seconds(len(papers))
    _LOCAL_SELECTION_PAGES[page_id] = {
        "selection_token": selection_token,
        "confirmation_token": confirmation_token,
        "papers": papers,
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
    }
    return {
        "page_id": page_id,
        "url": _local_selection_page_url(page_id),
        "selection_timeout_seconds": timeout_seconds if _is_download_selection_semantics(semantics) else 0,
        "selection_expires_at": (
            _LOCAL_SELECTION_PAGES[page_id].get("selection_expires_at", "")
            if _is_download_selection_semantics(semantics)
            else ""
        ),
    }


def _render_local_selection_html(page_id: str, page: Dict[str, Any]) -> str:
    from .ui.html_templates import _render_local_selection_html as _render_template

    return _render_template(page_id, page)

    papers = page.get("papers", [])
    rows = []
    for paper in papers if isinstance(papers, list) else []:
        index = paper.get("index")
        disabled = paper.get("parse_ready") is False or not isinstance(index, int)
        published = str(paper.get("published_date") or paper.get("year") or "Not available")
        venue = str(paper.get("publication_venue") or "Not available")
        original_url = str(paper.get("original_url") or paper.get("url") or paper.get("pdf_url") or "")
        doi = str(paper.get("doi") or "")
        source = str(paper.get("source") or "unknown")
        paper_id = str(paper.get("paper_id") or "")
        link_html = (
            '<a class="paper-link" href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'.format(
                href=html_escape(original_url, quote=True),
                label=html_escape(original_url),
            )
            if original_url
            else '<span class="muted-value">Not available</span>'
        )
        rows.append(
            """
            <label class="paper{disabled_class}">
              <input class="paper-check" type="checkbox" name="paper" value="{index}" {disabled}>
              <span class="paper-body">
                <span class="paper-title"><span class="index-no">{index}.</span> {title}</span>
                <span class="meta-grid">
                  <span><b>Published</b><em>{published}</em></span>
                  <span><b>Journal / Venue</b><em>{venue}</em></span>
                  <span><b>Source</b><em>{source}</em></span>
                  <span><b>Paper ID</b><em>{paper_id}</em></span>
                  <span><b>DOI</b><em>{doi}</em></span>
                  <span class="url-field"><b>Original URL</b><em>{link}</em></span>
                </span>
              </span>
            </label>
            """.format(
                disabled_class=" disabled" if disabled else "",
                index=html_escape(str(index or "")),
                disabled="disabled" if disabled else "",
                title=html_escape(str(paper.get("title") or "Untitled")),
                published=html_escape(published),
                venue=html_escape(venue),
                source=html_escape(source),
                paper_id=html_escape(paper_id or "Not available"),
                doi=html_escape(doi or "Not available"),
                link=link_html,
            )
        )

    body = "\n".join(rows) if rows else '<div class="empty">No papers available.</div>'
    data_json = html_escape(
        json.dumps(
            {
                "page_id": page_id,
                "selection_token": page.get("selection_token", ""),
                "save_path": page.get("save_path", DEFAULT_SAVE_PATH),
                "use_scihub": bool(page.get("use_scihub")),
                "mode": page.get("mode", "auto"),
                "backend": page.get("backend", ""),
                "force": bool(page.get("force")),
                "custom_save_path_confirmed": bool(page.get("custom_save_path_confirmed")),
                "selection_semantics": page.get("selection_semantics", SELECTION_SEMANTICS_PARSE),
                "parse_execution": page.get("parse_execution", "background"),
            }
        ),
        quote=True,
    )
    semantics = _selection_semantics_name(str(page.get("selection_semantics") or SELECTION_SEMANTICS_PARSE))
    action_label = "Download selected" if semantics == SELECTION_SEMANTICS_DOWNLOAD_ONLY else "Parse selected with MinerU"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper Scholar · MinerU Workspace</title>
  <style>
    /* ── Design Tokens: Academic Liquid Glass ── */
    :root {{
      color-scheme: light dark;
      /* Depth layers */
      --bg-deep: #0a1628;
      --bg-mid: #0f1d36;
      --bg-surface: #162744;
      /* Liquid glass — multi-stop frosted surfaces */
      --glass-thick: rgba(15, 30, 55, .82);
      --glass-medium: rgba(20, 38, 65, .68);
      --glass-light: rgba(25, 48, 78, .52);
      --glass-card: rgba(18, 32, 56, .54);
      --glass-card-hover: rgba(26, 46, 76, .72);
      --glass-toolbar: rgba(12, 24, 44, .52);
      --glass-progress: rgba(14, 28, 50, .66);
      /* Light blue palette */
      --accent: #6db3f2;
      --accent-glow: rgba(109, 179, 242, .35);
      --accent-soft: #5b9bd5;
      --primary: #78c6e7;
      --primary-glow: rgba(120, 198, 231, .28);
      --success: #4ec9b0;
      --success-glow: rgba(78, 201, 176, .30);
      --danger: #f97066;
      --danger-glow: rgba(249, 112, 102, .25);
      --warning: #6db3f2;
      /* Text & lines */
      --text-primary: #e2eaf5;
      --text-secondary: #9aaec9;
      --text-muted: #7a95b5;
      --text-ink: #d0dff0;
      --line-subtle: rgba(255, 255, 255, .08);
      --line-medium: rgba(255, 255, 255, .14);
      --line-glow: rgba(255, 255, 255, .22);
      /* Shadows & depth */
      --shadow-shell: 0 32px 100px rgba(0, 0, 0, .42), 0 0 0 1px rgba(255, 255, 255, .06) inset;
      --shadow-card: 0 4px 16px rgba(0, 0, 0, .22);
      --shadow-card-hover: 0 12px 32px rgba(0, 0, 0, .32), 0 0 0 1px rgba(109, 179, 242, .18);
      --shadow-button: 0 6px 20px rgba(0, 0, 0, .28), 0 0 0 1px rgba(109, 179, 242, .22);
      --shadow-progress-glow: 0 0 40px var(--accent-glow);
      /* Radii */
      --radius-shell: 16px;
      --radius-card: 10px;
      --radius-button: 10px;
      --radius-bar: 999px;
      /* Transitions */
      --ease-out-expo: cubic-bezier(.16, 1, .3, 1);
      --ease-out-back: cubic-bezier(.34, 1.56, .64, 1);
      --ease-spring: cubic-bezier(.4, 0, .2, 1);
    }}

    @media (prefers-color-scheme: light) {{
      :root {{
        --bg-deep: #dce8f5;
        --bg-mid: #e4eef8;
        --bg-surface: #ecf3fb;
        --glass-thick: rgba(255, 255, 255, .72);
        --glass-medium: rgba(255, 255, 255, .58);
        --glass-light: rgba(255, 255, 255, .45);
        --glass-card: rgba(255, 255, 255, .52);
        --glass-card-hover: rgba(255, 255, 255, .74);
        --glass-toolbar: rgba(255, 255, 255, .38);
        --glass-progress: rgba(255, 255, 255, .48);
        --text-primary: #1a2d4a;
        --text-secondary: #4a6078;
        --text-muted: #6d8199;
        --text-ink: #162237;
        --line-subtle: rgba(0, 0, 0, .06);
        --line-medium: rgba(0, 0, 0, .10);
        --line-glow: rgba(0, 0, 0, .15);
        --shadow-shell: 0 24px 80px rgba(30, 60, 100, .15), 0 0 0 1px rgba(255, 255, 255, .40) inset;
        --shadow-card: 0 2px 12px rgba(30, 60, 100, .06);
        --shadow-card-hover: 0 10px 28px rgba(48, 96, 160, .12), 0 0 0 1px rgba(74, 144, 217, .18);
        --shadow-button: 0 4px 14px rgba(0, 0, 0, .10), 0 0 0 1px rgba(74, 144, 217, .25);
        --shadow-progress-glow: 0 0 32px rgba(74, 144, 217, .18);
      }}
    }}

    /* ── Base ── */
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(ellipse 80% 60% at 20% 5%, rgba(74, 144, 217, .10), transparent 55%),
        radial-gradient(ellipse 60% 50% at 80% 15%, rgba(109, 179, 242, .07), transparent 50%),
        radial-gradient(ellipse 50% 40% at 50% 90%, rgba(53, 122, 189, .05), transparent 45%),
        linear-gradient(170deg, var(--bg-deep), var(--bg-mid) 40%, var(--bg-surface) 100%);
      color: var(--text-primary);
      font-family: "Segoe UI", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 14px;
      line-height: 1.52;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }}

    /* Subtle academic dot-grid pattern overlay */
    body::before {{
      content: "";
      position: fixed; inset: 0; pointer-events: none; z-index: 0;
      background-image: radial-gradient(circle, rgba(255,255,255,.03) 1px, transparent 1px);
      background-size: 24px 24px;
    }}

    main {{
      position: relative; z-index: 1;
      min-height: 100vh; padding: 32px 20px 40px;
      display: grid; place-items: start center;
    }}

    /* ── Shell: Multi-layered liquid glass ── */
    .shell {{
      width: min(78vw, 1480px); min-width: min(100%, 840px);
      margin: 0 auto;
      background:
        linear-gradient(175deg, var(--glass-thick), var(--glass-medium) 60%, var(--glass-light));
      border: 1px solid var(--line-glow);
      border-radius: var(--radius-shell);
      overflow: hidden;
      box-shadow: var(--shadow-shell);
      backdrop-filter: blur(32px) saturate(140%) brightness(1.02);
      -webkit-backdrop-filter: blur(32px) saturate(140%) brightness(1.02);
      /* Iridescent border shimmer */
      position: relative;
    }}
    .shell::before {{
      content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 0;
      border-radius: inherit;
      background: linear-gradient(135deg, rgba(255,255,255,.06) 0%, transparent 40%, rgba(74,144,217,.04) 70%, transparent 100%);
    }}

    /* ── Header ── */
    .shell-header {{
      position: relative; z-index: 1;
      display: flex; align-items: flex-start; justify-content: space-between;
      gap: 20px; padding: 26px 28px 20px;
      border-bottom: 1px solid var(--line-subtle);
      background: linear-gradient(180deg, rgba(255,255,255,.04), transparent);
    }}
    .shell-header h1 {{
      margin: 0;
      font-family: "Georgia", "Times New Roman", ui-serif, serif;
      font-size: 22px; font-weight: 700;
      color: var(--text-ink);
      letter-spacing: -.01em;
    }}
    .shell-header h1 .accent-dot {{
      display: inline-block; width: 8px; height: 8px;
      background: var(--accent); border-radius: 50%;
      margin-right: 6px;
      box-shadow: 0 0 12px var(--accent-glow);
      animation: dotPulse 2.4s ease-in-out infinite;
    }}
    @keyframes dotPulse {{
      0%, 100% {{ box-shadow: 0 0 8px var(--accent-glow); }}
      50% {{ box-shadow: 0 0 20px var(--accent-glow), 0 0 36px rgba(74,144,217,.18); }}
    }}
    .shell-header h1::after {{
      content: "Academic Literature · MinerU Parsing Workspace";
      display: block; margin-top: 5px;
      color: var(--text-muted); font-family: system-ui, sans-serif;
      font-size: 11px; font-weight: 500; letter-spacing: .04em;
    }}
    .token-badge {{
      max-width: 48%; padding: 8px 14px;
      background: rgba(255,255,255,.06); border: 1px solid var(--line-subtle);
      border-radius: 8px;
      color: var(--text-muted); font-size: 11px;
      font-family: "Cascadia Code", "Fira Code", ui-monospace, monospace;
      line-height: 1.4; overflow-wrap: anywhere; text-align: right;
    }}

    /* ── Paper list ── */
    .list {{
      position: relative; z-index: 1;
      display: grid; gap: 10px;
      max-height: 58vh; overflow: auto;
      padding: 14px 16px;
      scrollbar-width: thin;
      scrollbar-color: var(--line-medium) transparent;
    }}
    .list::-webkit-scrollbar {{ width: 6px; }}
    .list::-webkit-scrollbar-track {{ background: transparent; }}
    .list::-webkit-scrollbar-thumb {{
      background: var(--line-medium); border-radius: 3px;
    }}

    /* ── Paper card: liquid glass with gold accent hover ── */
    .paper {{
      display: grid; grid-template-columns: 32px minmax(0, 1fr);
      gap: 14px; align-items: start;
      padding: 15px 18px;
      border: 1px solid var(--line-subtle);
      border-radius: var(--radius-card);
      background: var(--glass-card);
      box-shadow: var(--shadow-card);
      cursor: pointer;
      transition:
        background .22s var(--ease-out-expo),
        border-color .22s var(--ease-out-expo),
        box-shadow .22s var(--ease-out-expo),
        transform .22s var(--ease-out-expo);
      position: relative; overflow: hidden;
    }}
    .paper::after {{
      content: ""; position: absolute; inset: 0; pointer-events: none;
      border-radius: inherit;
      background: radial-gradient(ellipse at 50% 0%, rgba(74,144,217,.06), transparent 70%);
      opacity: 0; transition: opacity .28s ease;
    }}
    .paper:hover {{
      background: var(--glass-card-hover);
      border-color: rgba(74, 144, 217, .28);
      box-shadow: var(--shadow-card-hover);
      transform: translateY(-2px);
    }}
    .paper:hover::after {{ opacity: 1; }}
    .paper.disabled {{
      background: rgba(100, 120, 140, .10);
      color: var(--text-muted); cursor: not-allowed;
    }}
    .paper.disabled:hover {{ transform: none; box-shadow: var(--shadow-card); border-color: var(--line-subtle); }}
    .paper.disabled:hover::after {{ opacity: 0; }}

    /* ── Custom checkbox ── */
    .paper-check {{
      -webkit-appearance: none; appearance: none;
      width: 22px; height: 22px; margin: 1px 0 0;
      border: 2px solid var(--line-medium); border-radius: 6px;
      background: rgba(255,255,255,.04);
      cursor: pointer; position: relative; flex-shrink: 0;
      transition: all .18s var(--ease-out-expo);
    }}
    .paper-check:checked {{
      background: var(--accent);
      border-color: var(--accent);
      box-shadow: 0 0 14px var(--accent-glow);
    }}
    .paper-check:checked::after {{
      content: "";
      position: absolute; top: 2px; left: 6px;
      width: 6px; height: 11px;
      border: solid #fff; border-width: 0 2px 2px 0;
      transform: rotate(45deg);
    }}
    .paper-check:hover:not(:disabled) {{
      border-color: var(--accent-soft);
      box-shadow: 0 0 8px rgba(74,144,217,.25);
    }}
    .paper-check:disabled {{ opacity: .35; cursor: not-allowed; }}

    .paper-body {{ min-width: 0; }}
    .index-no {{
      color: var(--accent); font-weight: 750;
      font-size: 13px; margin-right: 4px;
    }}
    .paper-title {{
      display: block;
      color: var(--text-ink);
      font-size: 15px; font-weight: 680;
      line-height: 1.4; overflow-wrap: anywhere;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px 16px; margin-top: 10px;
      color: var(--text-muted); font-size: 11px;
    }}
    .meta-grid span {{ min-width: 0; }}
    .meta-grid b {{
      display: block;
      color: var(--text-secondary);
      font-size: 9px; font-weight: 750; text-transform: uppercase;
      letter-spacing: .06em;
    }}
    .meta-grid em {{ display: block; font-style: normal; overflow-wrap: anywhere; }}
    .url-field {{ grid-column: span 2; }}
    .paper-link {{
      color: var(--primary); text-decoration: none;
      transition: color .14s ease;
    }}
    .paper-link:hover {{ color: var(--accent); text-decoration: underline; }}
    .muted-value {{ color: var(--text-muted); }}

    /* ── Toolbar ── */
    .toolbar {{
      position: relative; z-index: 1;
      display: flex; flex-wrap: wrap; align-items: center;
      gap: 10px; padding: 16px 20px 18px;
      border-top: 1px solid var(--line-subtle);
      background: var(--glass-toolbar);
    }}
    button {{
      min-height: 38px; border: 1px solid var(--line-medium);
      border-radius: var(--radius-button);
      background: rgba(255,255,255,.06);
      color: var(--text-primary);
      padding: 8px 16px;
      font: inherit; font-weight: 620;
      cursor: pointer;
      transition: all .18s var(--ease-out-expo);
      position: relative; overflow: hidden;
    }}
    button::after {{
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background: radial-gradient(circle at var(--rx, 50%) var(--ry, 50%), rgba(255,255,255,.12), transparent 70%);
      opacity: 0; transition: opacity .18s ease;
    }}
    button:hover:not(:disabled)::after {{ opacity: 1; }}
    button:hover:not(:disabled) {{
      transform: translateY(-1px);
      border-color: rgba(74,144,217,.35);
      box-shadow: 0 4px 16px rgba(0,0,0,.15);
    }}
    button:active:not(:disabled) {{ transform: scale(.97); }}

    button.primary {{
      background: linear-gradient(135deg, #357abd, #4a90d9);
      border-color: transparent;
      color: #1a1206;
      font-weight: 700;
      box-shadow: var(--shadow-button);
      letter-spacing: .02em;
    }}
    button.primary:hover:not(:disabled) {{
      background: linear-gradient(135deg, #4a90d9, #6db3f2);
      box-shadow: 0 8px 28px rgba(74,144,217,.30), 0 0 0 1px rgba(74,144,217,.30);
      transform: translateY(-2px);
    }}
    button:disabled {{ opacity: .42; cursor: not-allowed; }}

    .status {{
      margin-left: auto; min-height: 20px;
      color: var(--text-secondary); font-size: 12px;
      white-space: pre-wrap; max-width: 340px;
      transition: color .2s ease;
    }}
    .status.error {{ color: var(--danger); }}
    .status.success {{ color: var(--success); }}

    .selection-count {{
      min-width: 78px;
      border: 1px solid var(--line-medium); border-radius: var(--radius-button);
      background: rgba(255,255,255,.06);
      color: var(--text-ink); padding: 9px 16px;
      font-size: 15px; font-weight: 780; text-align: center;
      font-variant-numeric: tabular-nums;
      transition: all .25s var(--ease-out-back);
    }}
    .selection-count.pulse {{
      transform: scale(1.12);
      border-color: var(--accent);
      box-shadow: 0 0 16px var(--accent-glow);
    }}

    /* ── Progress Panel: hero component ── */
    .progress-panel {{
      position: relative; z-index: 1;
      margin: 0; padding: 20px 24px 22px;
      border-top: 1px solid var(--line-subtle);
      background: var(--glass-progress);
      animation: panelSlideIn .40s var(--ease-out-expo);
    }}
    @keyframes panelSlideIn {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}

    .progress-head {{
      display: flex; justify-content: space-between;
      gap: 16px; align-items: flex-start; margin-bottom: 14px;
    }}
    .progress-title {{
      margin: 0;
      font-family: "Georgia", "Times New Roman", ui-serif, serif;
      font-size: 17px; font-weight: 700;
      color: var(--text-ink);
      display: flex; align-items: center; gap: 10px;
    }}
    /* Status dot in title */
    .status-dot {{
      display: inline-block; width: 10px; height: 10px;
      border-radius: 50%;
      background: var(--warning);
      box-shadow: 0 0 10px var(--accent-glow);
      animation: statusPulse 1.6s ease-in-out infinite;
    }}
    .status-dot.done {{ background: var(--success); box-shadow: 0 0 10px var(--success-glow); animation: none; }}
    .status-dot.error {{ background: var(--danger); box-shadow: 0 0 10px var(--danger-glow); animation: none; }}
    @keyframes statusPulse {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: .55; transform: scale(1.25); }}
    }}

    .progress-current {{ color: var(--text-muted); font-size: 12px; margin-top: 4px; }}
    .progress-meta {{
      color: var(--text-secondary); font-size: 12px;
      text-align: right; font-variant-numeric: tabular-nums;
    }}

    /* Progress bar with shimmer */
    .progress-bar {{
      height: 12px; overflow: hidden;
      border: 1px solid var(--line-medium);
      border-radius: var(--radius-bar);
      background: rgba(0,0,0,.18);
      position: relative;
    }}
    .progress-fill {{
      width: 0%; height: 100%; border-radius: inherit;
      background: linear-gradient(90deg, #357abd, #4a90d9, #6db3f2);
      background-size: 200% 100%;
      animation: shimmerProgress 2s linear infinite;
      transition: width .35s var(--ease-out-expo);
      position: relative;
    }}
    .progress-fill::after {{
      content: ""; position: absolute; inset: 0;
      background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,.22) 50%, transparent 100%);
      animation: shimmerSweep 1.8s ease-in-out infinite;
    }}
    @keyframes shimmerProgress {{
      0% {{ background-position: 200% 0; }}
      100% {{ background-position: 0% 0; }}
    }}
    @keyframes shimmerSweep {{
      0%, 100% {{ transform: translateX(-100%); }}
      50% {{ transform: translateX(100%); }}
    }}

    /* Progress list — card per paper */
    .progress-list {{
      display: grid; gap: 8px; margin-top: 14px;
      max-height: 240px; overflow: auto; padding-right: 4px;
      scrollbar-width: thin;
    }}
    .progress-item {{
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr) 80px;
      gap: 12px; align-items: center;
      padding: 11px 14px;
      border: 1px solid var(--line-subtle);
      border-radius: var(--radius-card);
      background: rgba(255,255,255,.04);
      animation: itemFadeIn .30s var(--ease-out-expo) both;
      transition: all .22s ease;
    }}
    @keyframes itemFadeIn {{
      from {{ opacity: 0; transform: translateX(-8px); }}
      to {{ opacity: 1; transform: translateX(0); }}
    }}
    .progress-item.status-processing {{
      border-color: rgba(74,144,217,.30);
      box-shadow: 0 0 20px var(--accent-glow);
      animation: itemFadeIn .30s var(--ease-out-expo) both, processingGlow 2s ease-in-out infinite;
    }}
    @keyframes processingGlow {{
      0%, 100% {{ box-shadow: 0 0 14px var(--accent-glow); }}
      50% {{ box-shadow: 0 0 28px var(--accent-glow), 0 0 44px rgba(74,144,217,.10); }}
    }}
    .progress-item.status-completed {{
      border-color: rgba(78,201,176,.25);
    }}
    .progress-item.status-error {{
      border-color: rgba(244,135,113,.30);
    }}

    /* Status icon */
    .progress-icon {{
      width: 22px; height: 22px;
      display: grid; place-items: center;
      font-size: 14px; flex-shrink: 0;
    }}
    .progress-icon.queued {{ color: var(--text-muted); }}
    .progress-icon.processing {{
      color: var(--accent);
      animation: spinIcon 1.4s linear infinite;
    }}
    .progress-icon.completed {{ color: var(--success); }}
    .progress-icon.error {{ color: var(--danger); }}
    .progress-icon.skipped {{ color: var(--text-muted); }}
    @keyframes spinIcon {{
      from {{ transform: rotate(0deg); }}
      to {{ transform: rotate(360deg); }}
    }}

    .progress-name {{ min-width: 0; }}
    .progress-name strong {{
      display: block; color: var(--text-ink);
      font-size: 13px; line-height: 1.35; overflow-wrap: anywhere;
    }}
    .progress-name span {{
      display: block; margin-top: 2px;
      color: var(--text-muted); font-size: 11px; overflow-wrap: anywhere;
    }}
    .progress-percent {{
      color: var(--text-secondary); font-size: 13px;
      font-weight: 750; text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .progress-percent.done {{ color: var(--success); }}
    .progress-percent.error {{ color: var(--danger); }}

    .empty {{ padding: 40px 20px; color: var(--text-muted); text-align: center; font-style: italic; }}

    /* ── Confetti canvas ── */
    #confetti-canvas {{
      position: fixed; inset: 0; pointer-events: none; z-index: 9999;
    }}

    /* ── Responsive ── */
    @media (max-width: 1100px) {{
      .shell {{ width: min(100%, 980px); min-width: 0; }}
      .meta-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      main {{ padding: 8px; }}
      .shell-header {{ display: grid; }}
      .token-badge {{ max-width: none; text-align: left; }}
      .toolbar {{ align-items: stretch; }}
      .status {{ width: 100%; margin-left: 0; }}
      .selection-count {{ margin-left: auto; }}
      .meta-grid {{ grid-template-columns: 1fr; }}
      .url-field {{ grid-column: auto; }}
      .progress-item {{ grid-template-columns: 22px minmax(0, 1fr) 64px; gap: 8px; }}
    }}
  </style>
</head>
<body>
  <canvas id="confetti-canvas"></canvas>
  <main>
    <section class="shell">
      <header class="shell-header">
        <h1><span class="accent-dot"></span>Paper Scholar</h1>
        <div class="token-badge">{html_escape(str(page.get("selection_token", "")))}</div>
      </header>
      <form id="form" data-page="{data_json}">
        <div class="list">{body}</div>
        <div class="toolbar">
          <button class="primary" id="parse" type="submit">{html_escape(action_label)}</button>
          <button id="select-all" type="button">Select All</button>
          <button id="clear" type="button">Clear</button>
          <span class="status" id="status"></span>
          <span class="selection-count" id="selection-count">0/0</span>
        </div>
      </form>
      <section class="progress-panel" id="progress-panel" hidden>
        <div class="progress-head">
          <div>
            <p class="progress-title" id="progress-title">
              <span class="status-dot" id="status-dot"></span>
              <span id="progress-title-text">MinerU Parsing</span>
            </p>
            <div class="progress-current" id="progress-current"></div>
          </div>
          <div class="progress-meta" id="progress-meta">0%</div>
        </div>
        <div class="progress-bar" aria-hidden="true">
          <div class="progress-fill" id="progress-fill"></div>
        </div>
        <div class="progress-list" id="progress-list"></div>
      </section>
    </section>
  </main>
  <script>
    /* ── DOM refs ── */
    const form = document.getElementById("form");
    const parseButton = document.getElementById("parse");
    const statusNode = document.getElementById("status");
    const selectionCountNode = document.getElementById("selection-count");
    const progressPanel = document.getElementById("progress-panel");
    const progressTitleText = document.getElementById("progress-title-text");
    const progressCurrent = document.getElementById("progress-current");
    const progressMeta = document.getElementById("progress-meta");
    const progressFill = document.getElementById("progress-fill");
    const progressList = document.getElementById("progress-list");
    const statusDot = document.getElementById("status-dot");
    const confettiCanvas = document.getElementById("confetti-canvas");
    const data = JSON.parse(form.dataset.page);
    let pollTimer = null;
    let prevCompleted = 0;

    /* ── Status icon map ── */
    const STATUS_ICONS = {{
      queued: '○',
      processing: '↻',
      completed: '✓',
      error: '✗',
      skipped: '→',
      canceled: '✗',
    }};
    const STATUS_CLASS = {{
      queued: 'queued',
      processing: 'processing',
      completed: 'completed',
      error: 'error',
      skipped: 'skipped',
      canceled: 'error',
    }};

    /* ── Helpers ── */
    function selectedIndices() {{
      return Array.from(document.querySelectorAll('input[name="paper"]:checked')).map(el => el.value);
    }}

    function updateSelectionCount() {{
      const all = Array.from(document.querySelectorAll('input[name="paper"]'));
      const selected = all.filter(el => el.checked);
      selectionCountNode.textContent = `${{selected.length}}/${{all.length}}`;
      selectionCountNode.classList.add('pulse');
      setTimeout(() => selectionCountNode.classList.remove('pulse'), 260);
    }}

    function setStatus(msg, kind) {{
      statusNode.textContent = msg || "";
      statusNode.className = kind ? 'status ' + kind : 'status';
    }}

    function escHtml(v) {{
      return String(v ?? "").replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',\"'\":'&#39;'}})[ch]);
    }}

    function terminalJob(s) {{
      return ["completed","error","canceled","not_found","invalid_selection"].includes(String(s||"").toLowerCase());
    }}

    /* ── Ripple on buttons ── */
    document.querySelectorAll('button').forEach(btn => {{
      btn.addEventListener('mousemove', e => {{
        const r = btn.getBoundingClientRect();
        btn.style.setProperty('--rx', ((e.clientX - r.left) / r.width * 100) + '%');
        btn.style.setProperty('--ry', ((e.clientY - r.top) / r.height * 100) + '%');
      }});
    }});

    /* ── Confetti ── */
    let confettiParticles = [];
    let confettiAnimId = null;

    function launchConfetti() {{
      const w = window.innerWidth, h = window.innerHeight;
      confettiCanvas.width = w; confettiCanvas.height = h;
      confettiParticles = [];
      const colors = ['#6db3f2','#4a90d9','#5b9bd5','#4ec9b0','#f48771','#78c6e7','#fff'];
      for (let i = 0; i < 120; i++) {{
        confettiParticles.push({{
          x: Math.random() * w, y: -20 - Math.random() * 100,
          w: 6 + Math.random() * 8, h: 4 + Math.random() * 6,
          color: colors[Math.floor(Math.random() * colors.length)],
          vx: (Math.random() - .5) * 4, vy: 2 + Math.random() * 6,
          rot: Math.random() * 360, rotV: (Math.random() - .5) * 8,
          opacity: 1, delay: Math.random() * 800,
        }});
      }}
      if (!confettiAnimId) animateConfetti();
    }}

    function animateConfetti() {{
      const ctx = confettiCanvas.getContext('2d');
      const w = confettiCanvas.width, h = confettiCanvas.height;
      ctx.clearRect(0, 0, w, h);
      let active = false;
      const now = performance.now();
      for (const p of confettiParticles) {{
        if (now < p.delay) {{ active = true; continue; }}
        p.x += p.vx; p.y += p.vy; p.rot += p.rotV;
        p.vy += .12; p.opacity -= .004;
        if (p.opacity <= 0 || p.y > h + 60) continue;
        active = true;
        ctx.save(); ctx.globalAlpha = Math.max(0, p.opacity);
        ctx.translate(p.x, p.y); ctx.rotate(p.rot * Math.PI / 180);
        ctx.fillStyle = p.color; ctx.fillRect(-p.w/2, -p.h/2, p.w, p.h);
        ctx.restore();
      }}
      if (active) {{
        confettiAnimId = requestAnimationFrame(animateConfetti);
      }} else {{
        confettiAnimId = null; ctx.clearRect(0, 0, w, h);
      }}
    }}

    /* ── Render progress ── */
    function renderProgress(job) {{
      if (!job || typeof job !== 'object') return;
      const percent = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
      const total = Number(job.total || 0);
      const done = Number(job.completed_items || 0);
      const parsed = Number(job.parsed || 0);
      const failed = Number(job.failed || 0);
      const skipped = Number(job.skipped || 0);
      const status = String(job.status || 'running');

      progressPanel.hidden = false;
      const isDone = status === 'completed';
      progressTitleText.textContent = isDone ? 'Parsing Complete' : (status === 'error' ? 'Parsing Interrupted' : 'MinerU Parsing');
      progressCurrent.textContent = job.current || job.message || (job.job_id ? 'Job ' + job.job_id : '');
      progressMeta.textContent = `${{percent}}%  ·  ${{done}}/${{total}} done  ·  ${{parsed}} parsed  ·  ${{failed}} failed  ·  ${{skipped}} skipped`;

      statusDot.className = 'status-dot' + (isDone ? ' done' : (status === 'error' ? ' error' : ''));
      progressFill.style.width = percent + '%';

      const items = Array.isArray(job.items) ? job.items : [];
      progressList.innerHTML = items.map((item, idx) => {{
        const pct = Math.max(0, Math.min(100, Number(item.progress_percent || 0)));
        const st = item.status || 'queued';
        const icon = STATUS_ICONS[st] || '○';
        const cls = STATUS_CLASS[st] || 'queued';
        const title = item.title || ('Paper ' + (item.index || ''));
        const detail = item.message || st;
        const pctClass = st === 'completed' ? ' done' : (st === 'error' ? ' error' : '');
        return `
          <div class="progress-item status-${{cls}}" style="animation-delay:${{idx * 45}}ms">
            <span class="progress-icon ${{cls}}">${{icon}}</span>
            <div class="progress-name">
              <strong>${{escHtml(item.index ? item.index + '. ' + title : title)}}</strong>
              <span>${{escHtml(st)}} · ${{escHtml(detail)}}</span>
            </div>
            <span class="progress-percent${{pctClass}}">${{pct}}%</span>
          </div>
        `;
      }}).join('');
      progressList.scrollTop = progressList.scrollHeight;

      /* Confetti on completion */
      const newDone = (isDone ? total : done);
      if (newDone > prevCompleted && isDone) {{
        launchConfetti();
        setStatus('All ' + total + ' papers parsed successfully!', 'success');
      }}
      prevCompleted = newDone;

      if (terminalJob(status)) {{
        parseButton.disabled = false;
        progressFill.style.animation = 'none';
        if (!isDone && status !== 'completed') {{
          setStatus(job.message || ('Job ' + status + '.'), 'error');
        }}
      }}
    }}

    async function pollJob(jobId) {{
      if (!jobId) return;
      try {{
        const resp = await fetch('/api/parse-job/' + encodeURIComponent(jobId));
        const job = await resp.json();
        renderProgress(job);
        if (!terminalJob(job.status)) {{
          pollTimer = setTimeout(() => pollJob(jobId), 1500);
        }}
      }} catch (err) {{
        setStatus(err?.message || String(err), 'error');
        parseButton.disabled = false;
      }}
    }}

    /* ── Button events ── */
    document.getElementById("select-all").addEventListener("click", () => {{
      document.querySelectorAll('input[name="paper"]:not(:disabled)')
        .forEach(el => el.checked = true);
      updateSelectionCount();
    }});
    document.getElementById("clear").addEventListener("click", () => {{
      document.querySelectorAll('input[name="paper"]')
        .forEach(el => el.checked = false);
      updateSelectionCount();
    }});
    form.addEventListener("change", e => {{
      if (e.target?.matches?.('input[name="paper"]')) updateSelectionCount();
    }});

    /* ── Submit ── */
    form.addEventListener("submit", async e => {{
      e.preventDefault();
      const sel = selectedIndices();
      if (!sel.length) {{ setStatus('Select at least one paper.', 'error'); return; }}
      parseButton.disabled = true;
      const dlOnly = data.selection_semantics === 'download_selected_only';
      setStatus(dlOnly ? 'Downloading…' : 'Submitting parse job…');
      progressPanel.hidden = false;
      progressTitleText.textContent = dlOnly ? 'Download Started' : 'MinerU Parsing';
      progressCurrent.textContent = dlOnly ? 'Preparing downloads.' : 'Submitting selected papers.';
      progressMeta.textContent = '0%';
      statusDot.className = 'status-dot';
      progressFill.style.width = '0%';
      progressFill.style.animation = 'shimmerProgress 2s linear infinite';
      progressList.innerHTML = '';
      prevCompleted = 0;
      if (pollTimer) {{ clearTimeout(pollTimer); pollTimer = null; }}
      try {{
        const ep = dlOnly ? '/api/download-selection/' : '/api/parse-selection/';
        const resp = await fetch(ep + encodeURIComponent(data.page_id), {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{selected_indices: sel.join(',')}}),
        }});
        const body = await resp.json();
        if (dlOnly) {{
          progressPanel.hidden = true;
          setStatus(body.message || ('Downloaded ' + (body.downloaded||0) + ' paper(s).'));
          parseButton.disabled = false;
        }} else {{
          renderProgress(body);
          if (body.job_id) {{
            setStatus('MinerU parsing: ' + body.job_id);
            pollJob(body.job_id);
          }} else {{
            setStatus(body.message || body.status || 'Unable to submit parse job.', 'error');
            parseButton.disabled = false;
          }}
        }}
      }} catch (err) {{
        setStatus(err?.message || String(err), 'error');
        parseButton.disabled = false;
      }}
    }});

    /* ── Init ── */
    updateSelectionCount();
  </script>
</body>
</html>"""


class _LocalSelectionHandler(BaseHTTPRequestHandler):
    server_version = "PaperSearchLocalSelection/1.0"

    def do_GET(self) -> None:
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
        is_parse_downloaded_selection = False
        page_id = self._page_id_from_path("/api/parse-selection")
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
                    download_and_parse_selected_papers(
                        selection_token=parse_selection_token,
                        selected_indices=selected_indices or "all",
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
                        "terminal": True,
                        "selection_token": parse_selection_token,
                        "message": result.get("message", "MinerU parsing started."),
                    }
            elif is_download_selection:
                from .tools.orchestration import _run_download_selected_papers

                selection_confirmation = create_selection_confirmation_token(
                    selection_token=page["selection_token"],
                    selected_indices=selected_indices,
                    action="download",
                    save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                )
                if selection_confirmation.get("status") != "ok":
                    self._send_json(selection_confirmation, status=400)
                    return
                confirmed = consume_selection_confirmation_token(
                    selection_token=page["selection_token"],
                    selected_indices=selected_indices,
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
                        selected_indices=selected_indices,
                        save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                        use_scihub=bool(page.get("use_scihub")),
                        mode=page.get("mode", "auto"),
                        backend=page.get("backend", ""),
                        force=bool(page.get("force")),
                        parse_execution=page.get("parse_execution", "none"),
                        custom_save_path_confirmed=bool(page.get("custom_save_path_confirmed")),
                        large_batch_selection="never",
                        bypass_large_batch_selection=True,
                    )
                )
                page["confirmation_token"] = secrets.token_urlsafe(24)
                if isinstance(result, dict):
                    result["confirmation_token"] = page["confirmation_token"]
                    prompt = result.get("parse_prompt")
                    if isinstance(prompt, dict):
                        page["last_parse_prompt"] = prompt
            else:
                result = asyncio.run(
                    download_and_parse_selected_papers(
                        selection_token=page["selection_token"],
                        selected_indices=selected_indices,
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
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = progress_subscribe(job_id)
        try:
            snapshot = _parse_job_snapshot(job_id)
            self.wfile.write(
                f"data: {json.dumps(snapshot, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
            )
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=30)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                try:
                    body = json.loads(payload)
                except Exception:
                    body = {}
                status = str(body.get("status") or "").lower()
                if status in {"completed", "error", "failed", "canceled", "not_found"}:
                    self.wfile.write(b"event: done\ndata: {}\n\n")
                    self.wfile.flush()
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("local selection progress stream failed")
        finally:
            progress_unsubscribe(job_id, q)

PAPER_SELECTION_WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #dce8f5;
      --glass: rgba(255, 255, 255, .48);
      --glass-strong: rgba(255, 255, 255, .70);
      --paper: rgba(255, 255, 255, .52);
      --paper-hover: rgba(255, 255, 255, .74);
      --text: #1a2d4a;
      --muted: #5a7290;
      --line: rgba(26, 45, 74, .10);
      --line-strong: rgba(26, 45, 74, .16);
      --accent: #4a90d9;
      --accent-strong: #357abd;
      --accent-glow: rgba(74, 144, 217, .28);
      --accent-soft: #6db3f2;
      --ink: #162237;
      --danger: #c0392b;
      --disabled: rgba(148, 163, 184, .16);
      --shadow: 0 24px 80px rgba(30, 60, 100, .15);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0a1628;
        --glass: rgba(18, 32, 56, .58);
        --glass-strong: rgba(25, 42, 70, .72);
        --paper: rgba(20, 36, 58, .48);
        --paper-hover: rgba(28, 48, 76, .68);
        --text: #e2eaf5;
        --muted: #8aa4c0;
        --line: rgba(180, 200, 225, .12);
        --line-strong: rgba(180, 200, 225, .18);
        --accent: #6db3f2;
        --accent-strong: #5b9bd5;
        --accent-glow: rgba(109, 179, 242, .30);
        --accent-soft: #4a90d9;
        --ink: #d0dff0;
        --danger: #f97066;
        --disabled: rgba(71, 85, 105, .24);
        --shadow: 0 24px 80px rgba(0, 0, 0, .35);
      }
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: radial-gradient(ellipse 70% 50% at 15% 5%, rgba(74, 144, 217, .10), transparent 50%),
        radial-gradient(ellipse 55% 45% at 80% 12%, rgba(109, 179, 242, .08), transparent 48%),
        radial-gradient(ellipse 45% 35% at 50% 90%, rgba(53, 122, 189, .05), transparent 45%),
        linear-gradient(170deg, var(--bg), #e4eef8 40%, #ecf3fb 100%);
      color: var(--text);
      font-family: "Segoe UI", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 14px;
      line-height: 1.48;
      -webkit-font-smoothing: antialiased;
    }

    main {
      min-height: 100vh;
      padding: 28px 18px;
    }

    .shell {
      width: min(75vw, 1440px);
      min-width: min(100%, 820px);
      margin: 0 auto;
      background: linear-gradient(180deg, var(--glass-strong), var(--glass));
      border: 1px solid rgba(255,255,255,.56);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: var(--shadow), inset 0 1px 0 rgba(255,255,255,.20);
      backdrop-filter: blur(28px) saturate(150%);
      -webkit-backdrop-filter: blur(28px) saturate(150%);
      position: relative;
    }
    .shell::before {
      content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 0;
      border-radius: inherit;
      background: linear-gradient(135deg, rgba(255,255,255,.08) 0%, transparent 35%, rgba(74,144,217,.04) 65%, transparent 100%);
    }

    header {
      position: relative; z-index: 1;
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      padding: 24px 26px 20px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255, 255, 255, .20), transparent);
    }

    h1 {
      margin: 0;
      font-size: 21px;
      font-weight: 720;
      color: var(--ink);
      letter-spacing: -.01em;
    }

    h1::after {
      content: "MinerU-ready literature workspace";
      display: block;
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      letter-spacing: .02em;
    }

    .count {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      padding: 4px 10px;
      background: rgba(255,255,255,.25);
      border: 1px solid var(--line);
      border-radius: 6px;
    }

    .list {
      position: relative; z-index: 1;
      display: grid;
      gap: 10px;
      max-height: 62vh;
      overflow: auto;
      padding: 14px;
      scrollbar-width: thin;
      scrollbar-color: var(--line-strong) transparent;
    }
    .list::-webkit-scrollbar { width: 6px; }
    .list::-webkit-scrollbar-track { background: transparent; }
    .list::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 3px; }

    .paper {
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 13px;
      align-items: start;
      padding: 14px 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--paper);
      box-shadow: 0 2px 12px rgba(30, 60, 100, .06);
      cursor: pointer;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease, transform .18s ease;
      position: relative; overflow: hidden;
    }
    .paper::after {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      border-radius: inherit;
      background: radial-gradient(ellipse at 50% 0%, rgba(74,144,217,.05), transparent 65%);
      opacity: 0; transition: opacity .26s ease;
    }

    .paper:hover {
      background: var(--paper-hover);
      border-color: rgba(74, 144, 217, .30);
      box-shadow: 0 8px 28px rgba(48, 96, 160, .12), 0 0 0 1px rgba(74, 144, 217, .15);
      transform: translateY(-1px);
    }
    .paper:hover::after { opacity: 1; }
    .paper.disabled { background: var(--disabled); color: var(--muted); cursor: not-allowed; }
    .paper.disabled:hover { transform: none; box-shadow: 0 2px 12px rgba(30, 60, 100, .06); border-color: var(--line); }
    .paper.disabled:hover::after { opacity: 0; }

    input[type="checkbox"] {
      -webkit-appearance: none; appearance: none;
      width: 19px; height: 19px;
      margin: 2px 0 0;
      border: 2px solid var(--line-strong);
      border-radius: 5px;
      background: rgba(255,255,255,.30);
      cursor: pointer; flex-shrink: 0;
      position: relative;
      transition: all .16s ease;
    }
    input[type="checkbox"]:checked {
      background: var(--accent);
      border-color: var(--accent);
      box-shadow: 0 0 12px var(--accent-glow);
    }
    input[type="checkbox"]:checked::after {
      content: "";
      position: absolute; top: 2px; left: 5px;
      width: 5px; height: 9px;
      border: solid #fff; border-width: 0 2px 2px 0;
      transform: rotate(45deg);
    }
    input[type="checkbox"]:hover:not(:disabled) {
      border-color: var(--accent-soft);
      box-shadow: 0 0 8px var(--accent-glow);
    }
    input[type="checkbox"]:disabled { opacity: .35; cursor: not-allowed; }

    .paper-body { min-width: 0; }

    .index {
      color: var(--accent);
      font-weight: 750;
    }

    .title {
      display: block;
      color: var(--ink);
      font-size: 15px;
      overflow-wrap: anywhere;
      font-weight: 680;
      line-height: 1.4;
    }

    .meta-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px 14px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 11px;
    }

    .meta-grid span { min-width: 0; }

    .meta-grid b {
      display: block;
      color: color-mix(in srgb, var(--ink), var(--muted) 50%);
      font-size: 9px;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: .05em;
    }

    .meta-grid em {
      display: block;
      font-style: normal;
      overflow-wrap: anywhere;
    }

    .url-field { grid-column: span 2; }

    .paper-link {
      color: var(--accent-strong);
      text-decoration: none;
    }

    .paper-link:hover { text-decoration: underline; }

    .muted-value { color: var(--muted); }

    .toolbar {
      position: relative; z-index: 1;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 9px;
      padding: 15px 20px 17px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .15);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }

    button {
      min-height: 38px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: rgba(255, 255, 255, .40);
      color: var(--text);
      padding: 8px 15px;
      font: inherit;
      font-weight: 620;
      cursor: pointer;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .35);
      transition: transform .14s ease, border-color .14s ease, background .14s ease, box-shadow .14s ease;
      position: relative; overflow: hidden;
    }
    button::after {
      content: "";
      position: absolute; inset: 0; pointer-events: none;
      background: radial-gradient(circle at 50% 0%, rgba(255,255,255,.10), transparent 60%);
      opacity: 0; transition: opacity .18s ease;
    }

    button.primary {
      border-color: transparent;
      background: linear-gradient(135deg, #357abd, #4a90d9);
      color: #ffffff;
      font-weight: 680;
      box-shadow: 0 8px 22px rgba(53, 122, 189, .25);
      letter-spacing: .02em;
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      border-color: rgba(74, 144, 217, .35);
      background: rgba(255, 255, 255, .50);
      box-shadow: 0 4px 16px rgba(48, 96, 160, .12);
    }
    button:hover:not(:disabled)::after { opacity: 1; }

    button.primary:hover:not(:disabled) {
      background: linear-gradient(135deg, #4a90d9, #6db3f2);
      color: #ffffff;
      border-color: transparent;
      box-shadow: 0 10px 30px rgba(53, 122, 189, .35);
      transform: translateY(-2px);
    }

    button:active:not(:disabled) { transform: scale(.97); }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.50;
    }

    .status {
      min-height: 20px;
      margin-left: auto;
      color: var(--muted);
      font-size: 12px;
      white-space: pre-wrap;
    }

    .status.error { color: var(--danger); font-weight: 600; }

    .selection-count {
      min-width: 74px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: rgba(255, 255, 255, .40);
      color: var(--ink);
      padding: 9px 14px;
      font-size: 15px;
      font-weight: 780;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }

    .progress-panel {
      position: relative; z-index: 1;
      margin: 0;
      padding: 18px 22px 20px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .18);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }

    .progress-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
      margin-bottom: 12px;
    }

    .progress-title {
      margin: 0;
      color: var(--ink);
      font-size: 15px;
      font-weight: 750;
    }

    .progress-current {
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
    }

    .progress-meta {
      color: var(--muted);
      font-size: 12px;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }

    .progress-bar {
      height: 10px;
      overflow: hidden;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      background: rgba(26, 45, 74, .06);
      position: relative;
    }

    .progress-fill {
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #357abd, #4a90d9, #6db3f2);
      background-size: 200% 100%;
      animation: shimmerProgress 2.5s linear infinite;
      transition: width .30s ease;
      position: relative;
    }
    .progress-fill::after {
      content: "";
      position: absolute; inset: 0;
      background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,.28) 50%, transparent 100%);
      animation: shimmerSweep 2s ease-in-out infinite;
    }
    @keyframes shimmerProgress {
      0% { background-position: 200% 0; }
      100% { background-position: 0% 0; }
    }
    @keyframes shimmerSweep {
      0%, 100% { transform: translateX(-100%); }
      50% { transform: translateX(100%); }
    }

    .progress-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
      max-height: 220px;
      overflow: auto;
      padding-right: 4px;
      scrollbar-width: thin;
    }

    .progress-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 72px;
      gap: 12px;
      align-items: center;
      padding: 10px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, .30);
      transition: border-color .22s ease;
    }

    .progress-name { min-width: 0; }

    .progress-name strong {
      display: block;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .progress-name span {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 11px;
      overflow-wrap: anywhere;
    }

    .progress-percent {
      color: var(--accent);
      font-size: 13px;
      font-weight: 750;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }

    .empty {
      padding: 34px 20px;
      color: var(--muted);
      text-align: center;
    }

    @media (max-width: 1100px) {
      .shell { width: min(100%, 960px); min-width: 0; }
      .meta-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 640px) {
      main { padding: 10px; }
      header { display: grid; }
      .toolbar { align-items: stretch; }
      .status { width: 100%; margin-left: 0; }
      .selection-count { margin-left: auto; }
      .meta-grid { grid-template-columns: 1fr; }
      .url-field { grid-column: auto; }
    }
  </style>
</head>
<body>
  <main>
    <section class="shell" aria-labelledby="paper-selector-title">
      <header>
        <h1 id="paper-selector-title">Paper selector</h1>
        <div class="count" id="count"></div>
      </header>
      <form id="form">
        <div class="list" id="list"></div>
        <div class="toolbar">
          <button type="submit" class="primary" id="parse">Parse selected with MinerU</button>
          <button type="button" id="select-all">All</button>
          <button type="button" id="clear">Clear</button>
          <div class="status" id="status"></div>
          <div class="selection-count" id="selection-count">0/0</div>
        </div>
      </form>
      <section class="progress-panel" id="progress-panel" hidden>
        <div class="progress-head">
          <div>
            <p class="progress-title" id="progress-title">MinerU parsing started</p>
            <div class="progress-current" id="progress-current"></div>
          </div>
          <div class="progress-meta" id="progress-meta">0%</div>
        </div>
        <div class="progress-bar" aria-hidden="true"><div class="progress-fill" id="progress-fill"></div></div>
        <div class="progress-list" id="progress-list"></div>
      </section>
    </section>
  </main>
  <script>
    const list = document.getElementById("list");
    const count = document.getElementById("count");
    const form = document.getElementById("form");
    const parseButton = document.getElementById("parse");
    const selectAllButton = document.getElementById("select-all");
    const clearButton = document.getElementById("clear");
    const statusNode = document.getElementById("status");
    const selectionCountNode = document.getElementById("selection-count");
    const progressPanel = document.getElementById("progress-panel");
    const progressTitle = document.getElementById("progress-title");
    const progressCurrent = document.getElementById("progress-current");
    const progressMeta = document.getElementById("progress-meta");
    const progressFill = document.getElementById("progress-fill");
    const progressList = document.getElementById("progress-list");
    let rpcId = 1;
    const pending = new Map();
    let pollTimer = null;
    let data = normalizeSelectionData(unwrapToolOutput(window.openai?.toolOutput || {}));

    function unwrapToolOutput(value) {
      if (value?.result && typeof value.result === "object" && !Array.isArray(value.result)) {
        return value.result;
      }
      return value || {};
    }

    function normalizeSelectionData(value) {
      const root = value && typeof value === "object" ? value : {};
      const prompt = root.parse_prompt && typeof root.parse_prompt === "object" ? root.parse_prompt : null;
      if (prompt && Array.isArray(prompt.papers) && prompt.selection_token) {
        const needsSelection = !!prompt.parse_decision_required
          || prompt.recommended_tool === "render_paper_selection_app"
          || prompt.interaction === "backend_session_numbered_selection"
          || prompt.interaction === "mcp_app";
        return {
          ...root,
          ...prompt,
          save_path: prompt.save_path || root.save_path,
          use_scihub: prompt.use_scihub ?? root.use_scihub,
          mode: prompt.mode || root.mode,
          backend: prompt.backend ?? root.backend,
          force: prompt.force ?? root.force,
          custom_save_path_confirmed: prompt.custom_save_path_confirmed ?? root.custom_save_path_confirmed,
          selection_not_required: !needsSelection,
        };
      }
      return root;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;",
      })[ch]);
    }

    function setStatus(message, kind = "") {
      statusNode.textContent = message || "";
      statusNode.className = kind ? `status ${kind}` : "status";
    }

    function selectedIndices() {
      return Array.from(document.querySelectorAll('input[name="paper"]:checked')).map((item) => item.value);
    }

    function updateSelectionCount() {
      const all = Array.from(document.querySelectorAll('input[name="paper"]'));
      const selected = all.filter((item) => item.checked);
      selectionCountNode.textContent = `${selected.length}/${all.length}`;
    }

    function fieldValue(value) {
      const text = String(value ?? "").trim();
      return text || "Not available";
    }

    function originalUrl(paper) {
      return String(paper.original_url || paper.url || paper.pdf_url || "").trim();
    }

    function renderField(label, value, extraClass = "") {
      return `<span class="${extraClass}"><b>${escapeHtml(label)}</b><em>${escapeHtml(fieldValue(value))}</em></span>`;
    }

    function renderUrlField(paper) {
      const url = originalUrl(paper);
      const body = url
        ? `<a class="paper-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`
        : '<span class="muted-value">Not available</span>';
      return `<span class="url-field"><b>Original URL</b><em>${body}</em></span>`;
    }

    function render() {
      const downloadOnly = data.selection_semantics === "download_selected_only";
      const downloadAndParse = data.selection_semantics === "download_and_parse_selected_only";
      parseButton.textContent = downloadOnly
        ? "Download selected"
        : (downloadAndParse ? "Download and parse selected" : "Parse selected with MinerU");
      if (data.selection_not_required) {
        count.textContent = "";
        parseButton.disabled = true;
        selectAllButton.disabled = true;
        clearButton.disabled = true;
        updateSelectionCount();
        list.innerHTML = `<div class="empty">${escapeHtml(data.message || "No paper selection is required for this tool call.")}</div>`;
        updateSelectionCount();
        return;
      }

      const papers = Array.isArray(data.papers) ? data.papers : [];
      const ready = papers.filter((paper) => paper.parse_ready !== false);
      count.textContent = `${ready.length}/${papers.length} ready`;
      parseButton.disabled = ready.length === 0;
      selectAllButton.disabled = ready.length === 0;
      clearButton.disabled = ready.length === 0;

      if (!papers.length) {
        list.innerHTML = '<div class="empty">No papers available.</div>';
        updateSelectionCount();
        return;
      }

      list.innerHTML = papers.map((paper) => {
        const index = Number(paper.index);
        const disabled = paper.parse_ready === false || !Number.isFinite(index);
        return `
          <label class="paper${disabled ? " disabled" : ""}">
            <input type="checkbox" name="paper" value="${escapeHtml(index)}" ${disabled ? "disabled" : ""}>
            <span class="paper-body">
              <span class="title"><span class="index">${escapeHtml(index)}.</span> ${escapeHtml(paper.title || "Untitled")}</span>
              <span class="meta-grid">
                ${renderField("Published", paper.published_date || paper.year)}
                ${renderField("Journal / Venue", paper.publication_venue)}
                ${renderField("Source", paper.source || "unknown")}
                ${renderField("Paper ID", paper.paper_id)}
                ${renderField("DOI", paper.doi)}
                ${renderUrlField(paper)}
              </span>
            </span>
          </label>
        `;
      }).join("");
      updateSelectionCount();
    }

    function rpcRequest(method, params) {
      const id = rpcId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        window.setTimeout(() => {
          if (!pending.has(id)) return;
          pending.delete(id);
          reject(new Error("Timed out waiting for host response."));
        }, 120000);
      });
    }

    async function callTool(name, args) {
      if (window.openai?.callTool) {
        return window.openai.callTool(name, args);
      }
      return rpcRequest("tools/call", { name, arguments: args });
    }

    function structured(result) {
      const body = result?.structuredContent || result?.structured_content || result;
      return body?.result || body;
    }

    function terminalJob(status) {
      return ["completed", "error", "canceled", "not_found", "invalid_selection"].includes(String(status || "").toLowerCase());
    }

    function renderProgress(job) {
      if (!job || typeof job !== "object") return;
      const percent = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
      const total = Number(job.total || 0);
      const done = Number(job.completed_items || 0);
      const parsed = Number(job.parsed || 0);
      const failed = Number(job.failed || 0);
      const skipped = Number(job.skipped || 0);
      progressPanel.hidden = false;
      progressTitle.textContent = job.status === "completed" ? "MinerU parsing finished" : "MinerU parsing started";
      progressCurrent.textContent = job.current || job.message || (job.job_id ? `Job ${job.job_id}` : "");
      progressMeta.textContent = `${percent}% | ${done}/${total} done | ${parsed} parsed | ${failed} failed | ${skipped} skipped`;
      progressFill.style.width = `${percent}%`;
      const items = Array.isArray(job.items) ? job.items : [];
      progressList.innerHTML = items.map((item) => {
        const itemPercent = Math.max(0, Math.min(100, Number(item.progress_percent || 0)));
        const status = item.status || "queued";
        const title = item.title || `Paper ${item.index || ""}`;
        const detail = item.message || status;
        return `
          <div class="progress-item">
            <div class="progress-name">
              <strong>${escapeHtml(item.index ? `${item.index}. ${title}` : title)}</strong>
              <span>${escapeHtml(status)} | ${escapeHtml(detail)}</span>
            </div>
            <div class="progress-percent">${itemPercent}%</div>
          </div>
        `;
      }).join("");
      progressList.scrollTop = progressList.scrollHeight;
      if (terminalJob(job.status)) {
        parseButton.disabled = false;
        setStatus(job.status === "completed" ? "MinerU parsing finished." : (job.message || `Job ${job.status}.`), job.status === "completed" ? "" : "error");
      }
    }

    async function pollJob(jobId) {
      if (!jobId) return;
      try {
        const result = await callTool("get_parse_job_status", { job_id: jobId });
        const body = structured(result);
        renderProgress(body);
        if (!terminalJob(body?.status)) {
          pollTimer = window.setTimeout(() => pollJob(jobId), 1500);
        }
      } catch (error) {
        setStatus(error?.message || String(error), "error");
        parseButton.disabled = false;
      }
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const selected = selectedIndices();
      if (!selected.length) {
        setStatus("Select at least one paper.", "error");
        return;
      }

      parseButton.disabled = true;
      const downloadOnly = data.selection_semantics === "download_selected_only";
      const downloadAndParse = data.selection_semantics === "download_and_parse_selected_only";
      setStatus(downloadOnly ? "Downloading selected papers..." : "Submitting parse job...");
      progressPanel.hidden = downloadOnly;
      progressTitle.textContent = downloadOnly ? "Download started" : "MinerU parsing started";
      progressCurrent.textContent = downloadOnly ? "Downloading selected papers." : "Submitting selected papers.";
      progressMeta.textContent = "0%";
      progressFill.style.width = "0%";
      progressList.innerHTML = "";
      if (pollTimer) window.clearTimeout(pollTimer);
      try {
        const commonArgs = {
          selection_token: data.selection_token || "",
          selected_indices: selected.join(","),
          save_path: data.save_path || "~/Desktop/papers",
          use_scihub: !!data.use_scihub,
          mode: data.mode || "auto",
          backend: data.backend || "",
          force: !!data.force,
          custom_save_path_confirmed: !!data.custom_save_path_confirmed,
        };
        const result = downloadOnly
          ? await callTool("download_selected_papers", {
              ...commonArgs,
              parse_execution: "none",
              large_batch_selection: "never",
              bypass_large_batch_selection: true,
            })
          : await callTool(
              downloadAndParse ? "download_and_parse_selected_papers" : "submit_parse_job",
              commonArgs,
            );
        const body = structured(result);
        if (downloadOnly) {
          setStatus(body?.message || `Downloaded ${body?.downloaded || 0} paper(s).`);
          parseButton.disabled = false;
          return;
        }
        const jobId = body?.job_id || "";
        renderProgress(body);
        if (jobId) {
          setStatus(`MinerU parsing started: ${jobId}.`);
          pollJob(jobId);
        } else {
          setStatus(body?.message || body?.status || "Unable to submit parse job.", "error");
          parseButton.disabled = false;
        }
      } catch (error) {
        setStatus(error?.message || String(error), "error");
        parseButton.disabled = false;
      }
    });

    selectAllButton.addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]:not(:disabled)').forEach((item) => {
        item.checked = true;
      });
      updateSelectionCount();
      setStatus("");
    });

    clearButton.addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]').forEach((item) => {
        item.checked = false;
      });
      updateSelectionCount();
      setStatus("");
    });

    form.addEventListener("change", (event) => {
      if (event.target?.matches?.('input[name="paper"]')) {
        updateSelectionCount();
      }
    });

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;

      if (message.id && pending.has(message.id)) {
        const waiter = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) {
          waiter.reject(new Error(message.error.message || "Host returned an error."));
        } else {
          waiter.resolve(message.result);
        }
        return;
      }

      if (message.method === "ui/notifications/tool-result") {
        const next = message.params?.structuredContent;
        if (next && typeof next === "object") {
          data = unwrapToolOutput(next);
          render();
        }
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", () => {
      if (window.openai?.toolOutput) {
        data = normalizeSelectionData(unwrapToolOutput(window.openai.toolOutput));
        render();
      }
    }, { passive: true });

    render();
  </script>
</body>
</html>"""

from .ui.html_templates import PAPER_SELECTION_WIDGET_HTML as _SHARED_PAPER_SELECTION_WIDGET_HTML

PAPER_SELECTION_WIDGET_HTML = _SHARED_PAPER_SELECTION_WIDGET_HTML

MINERU_KEY_WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #eef3f7;
      --glass: rgba(255, 255, 255, .70);
      --glass-strong: rgba(255, 255, 255, .86);
      --input: rgba(255, 255, 255, .52);
      --text: #111827;
      --muted: #667085;
      --line: rgba(15, 23, 42, .13);
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --danger: #b42318;
      --shadow: 0 24px 70px rgba(15, 23, 42, .18);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --glass: rgba(22, 27, 34, .70);
        --glass-strong: rgba(31, 37, 46, .86);
        --input: rgba(15, 23, 42, .34);
        --text: #f8fafc;
        --muted: #aeb7c5;
        --line: rgba(226, 232, 240, .14);
        --accent: #2dd4bf;
        --accent-strong: #5eead4;
        --danger: #f97066;
        --shadow: 0 28px 80px rgba(0, 0, 0, .38);
      }
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(135deg, var(--bg), color-mix(in srgb, var(--bg), #dbeafe 28%));
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }

    main {
      min-height: 100vh;
      padding: 18px;
      display: grid;
      place-items: start center;
    }

    .panel {
      width: min(100%, 590px);
      background: linear-gradient(180deg, var(--glass-strong), var(--glass));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px) saturate(150%);
      -webkit-backdrop-filter: blur(22px) saturate(150%);
    }

    h1 {
      margin: 0 0 7px;
      font-size: 19px;
      font-weight: 700;
    }

    p {
      margin: 0 0 18px;
      color: var(--muted);
      max-width: 52ch;
    }

    label {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
      color: var(--text);
      font-weight: 650;
    }

    input {
      min-height: 42px;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--input);
      color: var(--text);
      padding: 9px 12px;
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .32);
    }

    input:focus {
      border-color: color-mix(in srgb, var(--accent), var(--line));
      outline: 3px solid color-mix(in srgb, var(--accent), transparent 74%);
      outline-offset: 0;
    }

    .row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }

    button {
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 8px;
      background: linear-gradient(180deg, var(--accent-strong), var(--accent));
      color: #fff;
      padding: 8px 14px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      box-shadow: 0 10px 24px rgba(15, 118, 110, .25);
      transition: transform .14s ease, filter .14s ease;
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      filter: brightness(1.04);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    .status {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
      white-space: pre-wrap;
    }

    .status.error { color: var(--danger); }

    @media (max-width: 560px) {
      main { padding: 10px; }
      .panel { padding: 16px; }
      .row { align-items: stretch; }
      .status { width: 100%; }
    }
  </style>
</head>
<body>
  <main>
    <form class="panel" id="form">
      <h1 id="title">Configure MinerU API key</h1>
      <p id="message">Enter your MinerU API key to enable official extract parsing.</p>
      <label>
        MinerU API key
        <input id="api-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste API key">
      </label>
      <div class="row">
        <button id="save" type="submit">Save key</button>
        <span class="status" id="status"></span>
      </div>
    </form>
  </main>
  <script>
    const form = document.getElementById("form");
    const input = document.getElementById("api-key");
    const button = document.getElementById("save");
    const message = document.getElementById("message");
    const statusNode = document.getElementById("status");
    let rpcId = 1;
    const pending = new Map();
    let data = window.openai?.toolOutput || {};

    function setStatus(text, kind = "") {
      statusNode.textContent = text || "";
      statusNode.className = kind ? `status ${kind}` : "status";
    }

    function render() {
      if (data.message) message.textContent = data.message;
      if (data.env_file_path) {
        input.setAttribute("aria-description", `Will save to ${data.env_file_path}`);
      }
    }

    function rpcRequest(method, params) {
      const id = rpcId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        window.setTimeout(() => {
          if (!pending.has(id)) return;
          pending.delete(id);
          reject(new Error("Timed out waiting for host response."));
        }, 60000);
      });
    }

    async function callTool(name, args) {
      if (window.openai?.callTool) {
        return window.openai.callTool(name, args);
      }
      return rpcRequest("tools/call", { name, arguments: args });
    }

    function structured(result) {
      return result?.structuredContent || result?.structured_content || result;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) {
        setStatus("Paste a MinerU API key first.", "error");
        return;
      }

      button.disabled = true;
      setStatus("Saving...");
      try {
        const result = await callTool("configure_mineru_api_key", {
          api_key: value,
        });
        const body = structured(result);
        input.value = "";
        setStatus(body?.message || "Saved.");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
      } finally {
        button.disabled = false;
      }
    });

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const payload = event.data;
      if (!payload || payload.jsonrpc !== "2.0") return;

      if (payload.id && pending.has(payload.id)) {
        const waiter = pending.get(payload.id);
        pending.delete(payload.id);
        if (payload.error) {
          waiter.reject(new Error(payload.error.message || "Host returned an error."));
        } else {
          waiter.resolve(payload.result);
        }
        return;
      }

      if (payload.method === "ui/notifications/tool-result") {
        const next = payload.params?.structuredContent;
        if (next && typeof next === "object") {
          data = next;
          render();
        }
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", () => {
      if (window.openai?.toolOutput) {
        data = window.openai.toolOutput;
        render();
      }
    }, { passive: true });

    render();
  </script>
</body>
</html>"""

# Instances of searchers
arxiv_searcher = ArxivSearcher()
pubmed_searcher = PubMedSearcher()
biorxiv_searcher = BioRxivSearcher()
medrxiv_searcher = MedRxivSearcher()
google_scholar_searcher = GoogleScholarSearcher()
iacr_searcher = IACRSearcher()
semantic_searcher = SemanticSearcher()
crossref_searcher = CrossRefSearcher()
openalex_searcher = OpenAlexSearcher()
pmc_searcher = PMCSearcher()
core_searcher = CORESearcher()
europepmc_searcher = EuropePMCSearcher()
dblp_searcher = DBLPSearcher()
openaire_searcher = OpenAiresearcher()
citeseerx_searcher = CiteSeerXSearcher()
doaj_searcher = DOAJSearcher()
base_searcher = BASESearcher()
unpaywall_resolver = UnpaywallResolver()
unpaywall_searcher = UnpaywallSearcher(resolver=unpaywall_resolver)
zenodo_searcher = ZenodoSearcher()
hal_searcher = HALSearcher()
ssrn_searcher = SSRNSearcher()

# ---------------------------------------------------------------------------
# Searcher registry for tools/ delegation
# ---------------------------------------------------------------------------
_SEARCHERS = {
    "arxiv": arxiv_searcher, "pubmed": pubmed_searcher,
    "biorxiv": biorxiv_searcher, "medrxiv": medrxiv_searcher,
    "google_scholar": google_scholar_searcher, "iacr": iacr_searcher,
    "semantic": semantic_searcher, "crossref": crossref_searcher,
    "openalex": openalex_searcher, "pmc": pmc_searcher,
    "core": core_searcher, "europepmc": europepmc_searcher,
    "dblp": dblp_searcher, "openaire": openaire_searcher,
    "citeseerx": citeseerx_searcher, "doaj": doaj_searcher,
    "base": base_searcher, "unpaywall": unpaywall_searcher,
    "zenodo": zenodo_searcher, "hal": hal_searcher,
    "ssrn": ssrn_searcher,
}


# ---------------------------------------------------------------------------
# Delegate tool registration to tools/ package
# ---------------------------------------------------------------------------
from .tools.cache import register_cache_tools
from .tools.parse import register_parse_tools
from .tools.widgets import register_widget_tools
from .tools.core import register_core_tools
from .tools.orchestration import register_orchestration_tools
from .tools.sources import register_source_tools

register_cache_tools(mcp)
register_parse_tools(mcp)
register_widget_tools(mcp)
register_core_tools(mcp)
register_orchestration_tools(mcp, _SEARCHERS)
register_source_tools(mcp, _SEARCHERS)

# Re-export search_papers for CLI backward compatibility.
# The function is set at module level in orchestration.py during registration.


async def search_papers(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "",
    year: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified search across all configured academic platforms.

    Delegates to the orchestration module's registered search_papers tool.
    """
    from .tools.orchestration import search_papers as _fn
    return await _fn(query, max_results_per_source=max_results_per_source,
                     sources=sources, year=year)

# scihub_searcher = SciHubSearcher()


# ---------------------------------------------------------------------------
# Backward-compatible shims for functions moved to modular tool modules.
# These are thin wrappers that delegate to the real implementations so
# existing tests and CLI commands continue to work.
# ---------------------------------------------------------------------------


async def search_arxiv(query: str, max_results: int = 10,
                       sort_by: str = 'relevance',
                       sort_order: str = 'descending') -> List[Dict]:
    """Search arXiv.  Delegates to the arxiv searcher directly."""
    return await async_search(arxiv_searcher, query, max_results,
                              sort_by=sort_by, sort_order=sort_order,
                              timeout=_env_float("ARXIV_TIMEOUT_SECONDS", 8.0, minimum=1.0),
                              max_attempts=_env_int("ARXIV_MAX_ATTEMPTS", 2, minimum=1))


async def search_pubmed(query: str, max_results: int = 10,
                         sort: str = 'relevance') -> List[Dict]:
    """Search PubMed.  Delegates to the pubmed searcher directly."""
    return await async_search(pubmed_searcher, query, max_results, sort=sort)


async def download_arxiv(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    parse_execution: str = "none",
    custom_save_path_confirmed: bool = False,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF from arXiv.  Delegates to the engine directly."""
    return await _download_source_pdf(
        arxiv_searcher,
        source="arxiv",
        paper_id=paper_id,
        save_path=save_path,
        parse_execution=parse_execution,
        custom_save_path_confirmed=custom_save_path_confirmed,
        ctx=ctx,
    )


async def download_semantic(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    parse_execution: str = "none",
    custom_save_path_confirmed: bool = False,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF from Semantic Scholar.  Delegates to the engine directly."""
    return await _download_source_pdf(
        semantic_searcher,
        source="semantic",
        paper_id=paper_id,
        save_path=save_path,
        parse_execution=parse_execution,
        custom_save_path_confirmed=custom_save_path_confirmed,
        ctx=ctx,
    )


async def configure_mineru_api_key(api_key: str) -> Dict[str, Any]:
    """Persist PAPER_SEARCH_MCP_MINERU_API_KEY to the project .env file."""
    value = api_key.strip()
    if not value:
        return {
            "status": "invalid_api_key",
            "message": "MinerU API key cannot be empty.",
            "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
        }
    target = await asyncio.to_thread(set_env_value, "MINERU_API_KEY", value)
    return {
        "status": "ok",
        "message": "MinerU API key saved. New parse requests will use the updated key.",
        "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
        "env_file_path": str(target),
        "configured": True,
    }


async def diagnose_paper_sources(sources: str = "all") -> Dict[str, Any]:
    """Report configured API keys, source capabilities, and disabled sources."""
    raw_sources = [part.strip().lower() for part in (sources or "").split(",") if part.strip()]
    if len(raw_sources) == 1 and raw_sources[0] in SEARCH_PROFILES:
        requested = [s for s in SEARCH_PROFILES[raw_sources[0]] if s in ALL_SOURCES]
    elif len(raw_sources) == 1 and raw_sources[0] in {"all", "deep"}:
        requested = list(ALL_SOURCES)
    elif raw_sources:
        requested = [s for s in raw_sources if s in ALL_SOURCES]
    else:
        requested = _parse_sources(sources)
    disabled = sorted(_disabled_sources())
    reports = [_source_capability_report(s) for s in requested]
    ranked_requested = _rank_sources_by_reliability(requested)
    return {
        "status": "ok",
        "sources_requested": sources,
        "sources_used": requested,
        "sources_ranked_by_reliability": ranked_requested,
        "disabled_sources": disabled,
        "_disabled_sources": disabled,
        "disable_env": f"PAPER_SEARCH_MCP_{DISABLED_SOURCES_ENV}",
        "default_agent_skill_fast_sources": [s for s in AGENT_SKILL_FAST_SOURCES if s not in _disabled_sources()],
        "agent_skill_broad_sources": [s for s in AGENT_SKILL_BROAD_SOURCES if s not in _disabled_sources()],
        "mineru": _source_capability_report("mineru"),
        "sources": reports,
        "notes": [
            "Missing Semantic Scholar key is not fatal; it mainly lowers rate limits.",
            "Zenodo public search/download does not require a token.",
            "SSRN has no public full-text API; PDF download is best-effort.",
            "Set PAPER_SEARCH_MCP_DISABLED_SOURCES to a comma-separated list to skip sources.",
        ],
    }


async def list_sources(include_capabilities: bool = True) -> Dict[str, Any]:
    """List configured academic sources and their capabilities."""
    srcs = []
    for source in ALL_SOURCES:
        entry: Dict[str, Any] = {"name": source}
        if include_capabilities:
            entry.update(SOURCE_CAPABILITIES.get(source, {}))
            entry["reliability"] = _source_reliability(source)
        srcs.append(entry)
    return {
        "sources": srcs,
        "sources_ranked_by_reliability": _rank_sources_by_reliability(ALL_SOURCES),
        "total": len(srcs),
    }


async def parse_pdf_with_mineru(
    pdf_path: str,
    paper_key: str = "",
    source: str = "",
    paper_id: str = "",
    doi: str = "",
    title: str = "",
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Parse a local PDF into cached Markdown, content_list JSON, manifest, and assets."""
    from .engine.parse import _attach_mineru_key_prompt
    from .parsers.mineru import parse_pdf_with_mineru as _parse_fn
    result = await asyncio.to_thread(
        _parse_fn, pdf_path,
        paper_key_hint=paper_key, source=source, paper_id=paper_id,
        doi=doi, title=title, mode=mode, backend=backend, force=force,
    )
    return _attach_mineru_key_prompt(result)


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
    from .tools.core import _run_submit_parse_job
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


async def get_parse_job_status(job_id: str) -> Dict[str, Any]:
    """Return current background parse job state and result when completed."""
    return _parse_job_snapshot(job_id)


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


async def mineru_api_key_setup_widget() -> str:
    """Return the MinerU API key setup UI for MCP Apps-capable hosts."""
    from .ui.html_templates import MINERU_KEY_WIDGET_HTML
    return MINERU_KEY_WIDGET_HTML


async def paper_selection_widget() -> str:
    """Return the paper-selection checkbox UI for MCP Apps-capable hosts."""
    from .ui.html_templates import PAPER_SELECTION_WIDGET_HTML
    return PAPER_SELECTION_WIDGET_HTML


async def render_paper_selection_app(
    selection_token: str,
    papers: Optional[List[Dict[str, Any]]] = None,
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
    selection_semantics: str = "",
    parse_execution: str = "",
) -> Any:
    """Render a checkbox paper selector for MCP Apps-capable hosts."""
    from .tools.widgets import _handle_render_paper_selection_app
    return await _handle_render_paper_selection_app(
        selection_token=selection_token, papers=papers,
        save_path=save_path, use_scihub=use_scihub,
        mode=mode, backend=backend, force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        selection_semantics=selection_semantics,
        parse_execution=parse_execution,
    )


async def open_paper_selection_page(
    selection_token: str,
    papers: Optional[List[Dict[str, Any]]] = None,
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
    selection_semantics: str = "",
    parse_execution: str = "",
    open_browser: bool = True,
) -> Dict[str, Any]:
    """Open a local browser checkbox selector for clients without MCP Apps UI."""
    from .tools.widgets import _handle_open_paper_selection_page
    return await _handle_open_paper_selection_page(
        selection_token=selection_token, papers=papers,
        save_path=save_path, use_scihub=use_scihub,
        mode=mode, backend=backend, force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        selection_semantics=selection_semantics,
        parse_execution=parse_execution, open_browser=open_browser,
    )


async def mineru_health_check(mode: str = "auto", backend: str = "") -> Dict[str, Any]:
    """Check MinerU API key setup and pypdf fallback status."""
    from .engine.parse import _mineru_key_setup_prompt
    from .parsers.mineru import mineru_health_check as _run_check
    result = await asyncio.to_thread(_run_check, mode=mode, backend=backend)
    extract_api = result.get("extract_api", {}) if isinstance(result, dict) else {}
    if not extract_api.get("ok"):
        result = {
            **result,
            "mineru_api_key_prompt": _mineru_key_setup_prompt(
                "missing",
                "MinerU API key is not configured. Enter it to enable official extract parsing.",
            ),
        }
    return result


async def mineru_setup_status() -> Dict[str, Any]:
    """Return MinerU API key setup status and an Apps prompt when configuration is missing."""
    from .engine.parse import _mineru_api_key_configured, _mineru_key_setup_prompt
    configured = _mineru_api_key_configured()
    if configured:
        return {
            "status": "ok",
            "configured": True,
            "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
            "env_file_path": str(env_file_path()),
            "message": "MinerU API key is configured.",
        }
    return {
        **_mineru_key_setup_prompt(
            "missing",
            "MinerU API key is not configured. Enter it to enable official extract parsing.",
        ),
        "configured": False,
    }


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
    """Parse papers from a saved search session by numbered selection."""
    from .tools.core import _run_parse_selected_papers
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
    from .tools.core import _run_download_and_parse_selected_papers
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


# Orchestration function shims — delegate to orchestration.py module-level refs
def _get_orch_fn(name: str):
    """Lazy-import an orchestration tool function by name."""
    from .tools.orchestration import (
        search_papers_for_parsing as _sfp,
        crawl_papers_for_selection as _cfs,
        search_papers_with_elicitation as _swe,
        download_with_fallback as _dwf,
        download_selected_papers as _dsp,
        resume_download as _rd,
        crawl_download_parse_papers as _cdpp,
        paper_research_workflow as _prw,
    )
    _map = {
        "search_papers_for_parsing": _sfp,
        "crawl_papers_for_selection": _cfs,
        "search_papers_with_elicitation": _swe,
        "download_with_fallback": _dwf,
        "download_selected_papers": _dsp,
        "resume_download": _rd,
        "crawl_download_parse_papers": _cdpp,
        "paper_research_workflow": _prw,
    }
    return _map[name]


async def search_papers_for_parsing(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "",
    year: Optional[str] = None,
) -> Dict[str, Any]:
    """Search papers, persist a numbered selection session, and return parse candidates."""
    return await _get_orch_fn("search_papers_for_parsing")(
        query=query, max_results_per_source=max_results_per_source,
        sources=sources, year=year,
    )


async def crawl_papers_for_selection(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "",
    year: Optional[str] = None,
    ranking_profile: str = "",
    requested_count: int = 0,
) -> Dict[str, Any]:
    """Search papers and persist a checkbox/numbered selection session."""
    return await _get_orch_fn("crawl_papers_for_selection")(
        query=query, max_results_per_source=max_results_per_source,
        sources=sources, year=year,
        ranking_profile=ranking_profile, requested_count=requested_count,
    )


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
    """Search papers, ask the MCP client for a multi-select choice, then parse."""
    return await _get_orch_fn("search_papers_with_elicitation")(
        query=query, max_results_per_source=max_results_per_source,
        sources=sources, year=year, save_path=save_path,
        use_scihub=use_scihub, mode=mode, backend=backend,
        force=force, ctx=ctx,
    )


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
    return await _get_orch_fn("download_with_fallback")(
        source=source, paper_id=paper_id, doi=doi, title=title,
        save_path=save_path, use_scihub=use_scihub,
        scihub_base_url=scihub_base_url, download_strategy=download_strategy,
        use_libgen=use_libgen, libgen_base_url=libgen_base_url,
        custom_save_path_confirmed=custom_save_path_confirmed, ctx=ctx,
    )


async def download_selected_papers(
    selection_token: str,
    selected_indices: str = "all",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
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
    """Download papers from a saved selection session without parsing by default."""
    return await _get_orch_fn("download_selected_papers")(
        selection_token=selection_token, selected_indices=selected_indices,
        save_path=save_path, use_scihub=use_scihub,
        concurrency=concurrency, parse_execution=parse_execution,
        mode=mode, backend=backend, force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        large_batch_selection=large_batch_selection,
        resume=resume, ctx=ctx,
    )


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
    """Resume a previous download session, skipping already-downloaded papers."""
    return await _get_orch_fn("resume_download")(
        selection_token=selection_token, save_path=save_path,
        use_scihub=use_scihub, concurrency=concurrency,
        parse_execution=parse_execution, mode=mode,
        backend=backend, force=force,
        custom_save_path_confirmed=custom_save_path_confirmed, ctx=ctx,
    )


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
    """Search, download top-ranked papers, then apply the parse policy."""
    return await _get_orch_fn("crawl_download_parse_papers")(
        query=query, count=count, max_results_per_source=max_results_per_source,
        sources=sources, year=year, ranking_profile=ranking_profile,
        save_path=save_path, use_scihub=use_scihub,
        download_concurrency=download_concurrency,
        parse_execution=parse_execution, parse_mode=parse_mode,
        backend=backend, force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        large_batch_selection=large_batch_selection,
        bypass_large_batch_selection=bypass_large_batch_selection, ctx=ctx,
    )


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
    parse_execution: str = "none",
    download_concurrency: int = 0,
    custom_save_path_confirmed: bool = False,
    large_batch_selection: str = "auto",
    bypass_large_batch_selection: bool = False,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Preferred MCP-first natural-language workflow for paper research."""
    return await _get_orch_fn("paper_research_workflow")(
        query=query, intent=intent, count=count,
        max_results_per_source=max_results_per_source,
        sources=sources, year=year, ranking_profile=ranking_profile,
        selection_mode=selection_mode, selected_indices=selected_indices,
        save_path=save_path, use_scihub=use_scihub,
        parse_mode=parse_mode, backend=backend, force=force,
        parse_execution=parse_execution, download_concurrency=download_concurrency,
        custom_save_path_confirmed=custom_save_path_confirmed,
        large_batch_selection=large_batch_selection,
        bypass_large_batch_selection=bypass_large_batch_selection, ctx=ctx,
    )


def _mcp_save_path_metadata(save_path: str, *, custom_save_path_confirmed: bool = False) -> Dict[str, Any]:
    resolved = resolve_save_path(save_path)
    default = resolve_save_path(DEFAULT_SAVE_PATH)
    return {
        "save_path": resolved,
        "default_save_path": default,
        "save_path_defaulted": resolved == default,
        "custom_save_path_confirmed": bool(custom_save_path_confirmed),
    }


# Asynchronous helper to adapt synchronous searchers
# Runs blocking requests-based calls in a thread pool to avoid blocking the event loop.


ALL_SOURCES = [
    "arxiv",
    "pubmed",
    "biorxiv",
    "medrxiv",
    "google_scholar",
    "iacr",
    "semantic",
    "crossref",
    "openalex",
    "pmc",
    "core",
    "europepmc",
    "dblp",
    "openaire",
    "citeseerx",
    "doaj",
    "base",
    "zenodo",
    "hal",
    "ssrn",
    "unpaywall",
]

FAST_SOURCES = [
    "arxiv",
    "semantic",
    "openalex",
    "crossref",
    "pubmed",
    "pmc",
    "europepmc",
]

PDF_CS_SOURCES = [
    "arxiv",
    "openalex",
    "crossref",
    "dblp",
]

AGENT_SKILL_FAST_SOURCES = [
    "arxiv",
    "openalex",
    "crossref",
]

AGENT_SKILL_BROAD_SOURCES = [
    "arxiv",
    "openalex",
    "crossref",
    "semantic",
    "google_scholar",
]

DEEP_SOURCES = list(ALL_SOURCES)

SEARCH_PROFILES: Dict[str, List[str]] = {
    "fast": FAST_SOURCES,
    "default": FAST_SOURCES,
    "pdf-cs": PDF_CS_SOURCES,
    "cs-pdf": PDF_CS_SOURCES,
    "pdf_cs": PDF_CS_SOURCES,
    "cs_pdf": PDF_CS_SOURCES,
    "agent-skill-fast": AGENT_SKILL_FAST_SOURCES,
    "agent_skill_fast": AGENT_SKILL_FAST_SOURCES,
    "agent-skill-broad": AGENT_SKILL_BROAD_SOURCES,
    "agent_skill_broad": AGENT_SKILL_BROAD_SOURCES,
    "deep": DEEP_SOURCES,
    "all": ALL_SOURCES,
}


SOURCE_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "arxiv": {"search": True, "download": True, "read": True, "notes": "Open API; reliable PDF/read."},
    "pubmed": {"search": True, "download": False, "read": False, "notes": "Metadata only; use DOI/PMC fallback."},
    "biorxiv": {"search": True, "download": True, "read": True, "notes": "Recent category-filtered preprints."},
    "medrxiv": {"search": True, "download": True, "read": True, "notes": "Recent category-filtered preprints."},
    "google_scholar": {"search": True, "download": False, "read": False, "notes": "Discovery only; bot-detection prone."},
    "iacr": {"search": True, "download": True, "read": True, "notes": "IACR ePrint PDFs."},
    "semantic": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Works when an openAccessPdf URL is available."},
    "crossref": {"search": True, "download": False, "read": False, "notes": "DOI and metadata backbone."},
    "openalex": {"search": True, "download": False, "read": False, "notes": "Metadata and OA links; does not host PDFs."},
    "pmc": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Open-access PMC PDFs."},
    "core": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "CORE key recommended."},
    "europepmc": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Biomedical OA PDFs when available."},
    "dblp": {"search": True, "download": False, "read": False, "notes": "Computer science metadata only."},
    "openaire": {"search": True, "download": False, "read": False, "notes": "OA discovery links; direct tool is metadata-oriented."},
    "citeseerx": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "Upstream availability varies."},
    "doaj": {"search": True, "download": "url_dependent", "read": "url_dependent", "notes": "Open-access journal records."},
    "base": {"search": "institution_dependent", "download": "record_dependent", "read": "record_dependent", "notes": "OAI-PMH may need registered IP."},
    "zenodo": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "Open repository files."},
    "hal": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "HAL documents with public PDF."},
    "ssrn": {"search": "best_effort", "download": "public_pdf_only", "read": "public_pdf_only", "notes": "SSRN bot/login restrictions vary."},
    "unpaywall": {"search": "doi_lookup", "download": False, "read": False, "notes": "OA URL resolver; requires email."},
}

SOURCE_RELIABILITY_SCORES: Dict[str, Dict[str, Any]] = {
    "arxiv": {
        "score": 95,
        "tier": "primary_pdf",
        "cs_relevant": True,
        "pdf_first": True,
        "notes": "Official CS-heavy preprint source with stable PDF identifiers.",
    },
    "openalex": {
        "score": 82,
        "tier": "oa_discovery",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Broad metadata and OA link discovery; often points back to arXiv PDFs.",
    },
    "crossref": {
        "score": 72,
        "tier": "doi_backbone",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "DOI metadata backbone; PDF links are useful but record-dependent.",
    },
    "dblp": {
        "score": 68,
        "tier": "cs_metadata",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "High-quality CS bibliography used for DOI/title enrichment; no hosted PDFs.",
    },
    "semantic": {
        "score": 48,
        "tier": "conditional_oa_pdf",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Useful when openAccessPdf is present; unauthenticated requests are rate-limited.",
    },
    "iacr": {
        "score": 42,
        "tier": "conditional_pdf",
        "cs_relevant": True,
        "pdf_first": True,
        "notes": "Cryptography-focused PDFs; current smoke test found direct download blocked.",
    },
    "core": {
        "score": 38,
        "tier": "repository",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Repository records are PDF-dependent and benefit from an API key.",
    },
    "citeseerx": {
        "score": 28,
        "tier": "legacy_repository",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Legacy CS source; current access path is timeout-prone.",
    },
    "google_scholar": {
        "score": 20,
        "tier": "discovery_only",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Discovery only and anti-bot prone; not suitable for automated PDF retrieval.",
    },
    "pmc": {"score": 50, "tier": "biomedical_oa_pdf", "cs_relevant": False, "pdf_first": False},
    "europepmc": {"score": 46, "tier": "biomedical_oa_pdf", "cs_relevant": False, "pdf_first": False},
    "biorxiv": {"score": 44, "tier": "life_science_preprint", "cs_relevant": False, "pdf_first": True},
    "medrxiv": {"score": 42, "tier": "medical_preprint", "cs_relevant": False, "pdf_first": True},
    "pubmed": {"score": 32, "tier": "biomedical_metadata", "cs_relevant": False, "pdf_first": False},
    "zenodo": {"score": 45, "tier": "repository", "cs_relevant": False, "pdf_first": False},
    "hal": {"score": 43, "tier": "repository", "cs_relevant": False, "pdf_first": False},
    "openaire": {"score": 40, "tier": "oa_discovery", "cs_relevant": False, "pdf_first": False},
    "doaj": {"score": 35, "tier": "journal_directory", "cs_relevant": False, "pdf_first": False},
    "base": {"score": 34, "tier": "repository", "cs_relevant": False, "pdf_first": False},
    "unpaywall": {"score": 36, "tier": "doi_oa_resolver", "cs_relevant": False, "pdf_first": False},
    "ssrn": {"score": 24, "tier": "social_science", "cs_relevant": False, "pdf_first": False},
}


# ---------------------------------------------------------------------------
# Optional paid-platform connectors (disabled by default)
# Set PAPER_SEARCH_MCP_IEEE_API_KEY / PAPER_SEARCH_MCP_ACM_API_KEY to activate
# (legacy IEEE_API_KEY / ACM_API_KEY are also supported).
# ---------------------------------------------------------------------------
_ieee_api_key = get_env("IEEE_API_KEY", "")
_acm_api_key = get_env("ACM_API_KEY", "")

if _ieee_api_key:
    from .academic_platforms.ieee import IEEESearcher
    ieee_searcher = IEEESearcher()
    ALL_SOURCES.append("ieee")
    DEEP_SOURCES.append("ieee")
    SOURCE_CAPABILITIES["ieee"] = {
        "search": True,
        "download": False,
        "read": False,
        "notes": "Metadata API search via query_text; PDF requires institutional IEEE access.",
    }
    logger.info("IEEE Xplore enabled via configured environment key.")
else:
    ieee_searcher = None

if _acm_api_key:
    from .academic_platforms.acm import ACMSearcher
    acm_searcher = ACMSearcher()
    ALL_SOURCES.append("acm")
    DEEP_SOURCES.append("acm")
    SOURCE_CAPABILITIES["acm"] = {
        "search": "skeleton",
        "download": "skeleton",
        "read": "skeleton",
        "notes": "Registered only with key; implementation currently raises NotImplementedError.",
    }
    logger.info("ACM Digital Library enabled via configured environment key.")
else:
    acm_searcher = None


for _searcher in [
    arxiv_searcher,
    pubmed_searcher,
    biorxiv_searcher,
    medrxiv_searcher,
    google_scholar_searcher,
    iacr_searcher,
    semantic_searcher,
    crossref_searcher,
    openalex_searcher,
    pmc_searcher,
    core_searcher,
    europepmc_searcher,
    dblp_searcher,
    openaire_searcher,
    citeseerx_searcher,
    doaj_searcher,
    base_searcher,
    unpaywall_searcher,
    zenodo_searcher,
    hal_searcher,
    ssrn_searcher,
    ieee_searcher,
    acm_searcher,
]:
    _wrap_save_path_methods(_searcher)


SOURCE_CONFIG_KEYS: Dict[str, List[str]] = {
    "semantic": ["PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"],
    "core": ["PAPER_SEARCH_MCP_CORE_API_KEY", "CORE_API_KEY"],
    "unpaywall": ["PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "UNPAYWALL_EMAIL"],
    "zenodo": ["PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN", "ZENODO_ACCESS_TOKEN"],
    "openaire": ["PAPER_SEARCH_MCP_OPENAIRE_API_KEY", "OPENAIRE_API_KEY"],
    "ieee": ["PAPER_SEARCH_MCP_IEEE_API_KEY", "IEEE_API_KEY"],
    "acm": ["PAPER_SEARCH_MCP_ACM_API_KEY", "ACM_API_KEY"],
    "mineru": ["PAPER_SEARCH_MCP_MINERU_API_KEY", "MINERU_API_KEY"],
}


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
    parse_execution: str = "none",
) -> Dict[str, Any]:
    surface = _selection_surface_policy(force_open=force_open)
    prompt["selection_surface"] = surface
    if surface.get("surface") == "numbered_fallback":
        return prompt
    if surface.get("surface") == "mcp_app":
        from .utils import host_mcp_apps_confirmed  # noqa: PLC0415

        if host_mcp_apps_confirmed():
            return prompt
    # 已有本地浏览器页面，不重复创建
    if prompt.get("local_browser", {}).get("url"):
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
        )
        prompt["local_browser"]["selection_surface"] = surface
        prompt["local_browser"]["selection_timeout_seconds"] = int(
            prompt["local_browser"].get("selection_timeout_seconds")
            or prompt["local_browser"].get("selection_timeout")
            or 0
        )
        if surface.get("surface") == "hybrid":
            prompt["interaction"] = "mcp_app"
            prompt["recommended_tool"] = PAPER_SELECTION_WIDGET_TOOL
            prompt["recommended_url"] = prompt["local_browser"].get("url", "")
            prompt["local_browser_url"] = prompt["local_browser"].get("url", "")
            prompt["page_id"] = prompt["local_browser"].get("page_id", "")
            prompt["opened"] = bool(prompt["local_browser"].get("opened", False))
    except Exception as exc:
        logger.exception("Failed to open local paper selection UI")
        prompt["local_browser"] = {
            "status": "error",
            "message": str(exc),
        }
    return prompt


def _arxiv_metadata_for_id(arxiv_id: str) -> Dict[str, Any]:
    paper_id = _extract_arxiv_id(arxiv_id)
    if not paper_id:
        return {}
    try:
        paper = arxiv_searcher.get_by_id(
            paper_id,
            timeout=_env_float(SEARCH_SOURCE_TIMEOUT_ENV, 8.0, minimum=1.0),
            max_attempts=_env_int("ARXIV_MAX_ATTEMPTS", 2, minimum=1),
        )
    except Exception as exc:
        logger.debug("arXiv metadata lookup failed for %s: %s", paper_id, exc)
        return {}
    if not paper:
        return {}
    data = paper.to_dict() if hasattr(paper, "to_dict") else dict(paper)
    found_id = _extract_arxiv_id(
        data.get("paper_id"),
        data.get("doi"),
        data.get("pdf_url"),
        data.get("url"),
    )
    data["source"] = "arxiv"
    data["paper_id"] = found_id or paper_id
    return data


async def _try_repository_fallback(
    doi: str,
    title: str,
    save_path: str,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[Optional[str], str]:
    repository_searchers = [
        ("openaire", openaire_searcher),
        ("core", core_searcher),
        ("europepmc", europepmc_searcher),
        ("pmc", pmc_searcher),
    ]

    query_candidates = [(doi or "").strip(), (title or "").strip()]
    query_candidates = [candidate for candidate in query_candidates if candidate]
    if not query_candidates:
        return None, "no DOI/title provided for repository fallback"

    repository_errors: List[str] = []

    for repo_name, searcher in repository_searchers:
        for query in query_candidates:
            try:
                papers = await asyncio.to_thread(searcher.search, query, max_results=3)
            except Exception as exc:
                repository_errors.append(f"{repo_name}:{exc}")
                continue

            if not papers:
                continue

            for paper in papers:
                if not _repository_paper_matches_request(paper, doi=doi, title=title):
                    repository_errors.append(f"{repo_name}: candidate did not match requested DOI/title")
                    continue
                pdf_url = str(getattr(paper, "pdf_url", "") or "").strip()
                if not pdf_url:
                    continue

                raw_paper_id = getattr(paper, "paper_id", "")
                paper_id = str(raw_paper_id or query).strip()
                filename_hint = _canonical_pdf_stem(
                    source=repo_name,
                    paper_id=paper_id,
                    doi=doi or _paper_value(getattr(paper, "doi", "")),
                    title=title or _paper_value(getattr(paper, "title", "")),
                    pdf_url=pdf_url,
                    url=_paper_value(getattr(paper, "url", "")),
                    fallback=f"{repo_name}_{paper_id}",
                )
                downloaded = await _download_from_url(pdf_url, save_path, filename_hint, client=client)
                if downloaded:
                    return downloaded, ""

    return None, "; ".join(repository_errors)


async def _try_primary_download(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
) -> Dict[str, Any]:
    downloader = _primary_downloaders().get(source_name)
    if downloader is None:
        return {
            "method": "primary",
            "path": None,
            "error": f"Unsupported source '{source_name}' for primary download.",
        }

    try:
        primary_result = await asyncio.to_thread(downloader, paper_id, save_path)
    except Exception as exc:
        logger.warning("Primary download failed for %s/%s: %s", source_name, paper_id, exc)
        return {"method": "primary", "path": None, "error": str(exc)}

    if _looks_like_pdf_path(primary_result):
        if os.path.exists(primary_result):
            record_download(
                pdf_path=primary_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader=f"{source_name}.download_pdf",
                legal_status="source_native_or_open_access",
            )
        return {
            "method": "primary",
            "path": primary_result,
            "downloader": f"{source_name}.download_pdf",
            "legal_status": "source_native_or_open_access",
        }

    return {"method": "primary", "path": None, "error": str(primary_result or "no PDF returned")}


async def _try_repository_download(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    repository_result, repository_error = await _try_repository_fallback(
        doi,
        title,
        save_path,
        client=client,
    )
    if repository_result:
        if os.path.exists(repository_result):
            record_download(
                pdf_path=repository_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader="repository_fallback",
                legal_status="open_access_repository",
            )
        return {
            "method": "repositories",
            "path": repository_result,
            "downloader": "repository_fallback",
            "legal_status": "open_access_repository",
        }
    return {"method": "repositories", "path": None, "error": repository_error or "no repository PDF found"}


async def _try_unpaywall_download(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    normalized_doi = (doi or "").strip()
    if not normalized_doi:
        return {"method": "unpaywall", "path": None, "error": "DOI not provided"}

    try:
        unpaywall_url = await asyncio.to_thread(unpaywall_resolver.resolve_best_pdf_url, normalized_doi)
    except Exception as exc:
        return {"method": "unpaywall", "path": None, "error": str(exc)}

    if not unpaywall_url:
        return {
            "method": "unpaywall",
            "path": None,
            "error": "no OA URL found (or PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL missing)",
        }

    unpaywall_result = await _download_from_url(unpaywall_url, save_path, f"unpaywall_{normalized_doi}", client=client)
    if unpaywall_result:
        if os.path.exists(unpaywall_result):
            record_download(
                pdf_path=unpaywall_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader="unpaywall",
                legal_status="open_access_unpaywall",
            )
        return {
            "method": "unpaywall",
            "path": unpaywall_result,
            "downloader": "unpaywall",
            "legal_status": "open_access_unpaywall",
        }
    return {"method": "unpaywall", "path": None, "error": "resolved OA URL but download failed"}


async def _try_publisher_direct_download_legacy(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    from .academic_platforms.publisher_direct import resolve_publisher_direct_url

    direct_url = resolve_publisher_direct_url(doi)
    if not direct_url:
        return {"method": "publisher_direct", "path": None, "error": "no known OA publisher direct URL"}
    filename_hint = _canonical_pdf_stem(
        source="publisher_direct",
        paper_id=paper_id,
        doi=doi,
        title=title,
        pdf_url=direct_url,
        fallback=f"publisher_direct_{doi or paper_id or title}",
    )
    downloaded = await _download_from_url(direct_url, save_path, filename_hint, client=client)
    if downloaded and os.path.exists(downloaded):
        record_download(
            pdf_path=downloaded,
            source=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="publisher_direct",
            legal_status="open_access_publisher_direct",
        )
        return {
            "method": "publisher_direct",
            "path": downloaded,
            "downloader": "publisher_direct",
            "legal_status": "open_access_publisher_direct",
        }
    return {"method": "publisher_direct", "path": None, "error": "direct OA URL failed PDF validation or download"}


async def _try_libgen_download_legacy(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
    libgen_base_url: str = "",
) -> Dict[str, Any]:
    identifier = (doi or "").strip() or (title or "").strip() or paper_id
    if not identifier:
        return {"method": "libgen", "path": None, "error": "no DOI, title, or paper_id provided"}
    try:
        from .academic_platforms.libgen import LibGenFetcher

        fetcher = LibGenFetcher(
            base_url=libgen_base_url or get_env("LIBGEN_BASE_URL", ""),
            output_dir=save_path,
            timeout=_env_float(DOWNLOAD_TIMEOUT_ENV, 30.0, minimum=1.0),
        )
        result = await asyncio.to_thread(fetcher.download_pdf, identifier)
    except Exception as exc:
        return {"method": "libgen", "path": None, "error": str(exc)}
    if result and os.path.exists(result) and _is_valid_pdf_file(result):
        record_download(
            pdf_path=result,
            source=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="libgen",
            legal_status="user_opt_in_libgen",
        )
        return {
            "method": "libgen",
            "path": result,
            "downloader": "libgen",
            "legal_status": "user_opt_in_libgen",
        }
    return {"method": "libgen", "path": None, "error": "LibGen did not return a valid PDF"}


async def _attempt_download_method_legacy(
    *,
    method: str,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
    client: Optional[httpx.AsyncClient] = None,
    libgen_base_url: str = "",
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        if method == "publisher_direct":
            result = await _try_publisher_direct_download_legacy(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                client=client,
            )
        elif method == "primary":
            result = await _try_primary_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
            )
        elif method == "repositories":
            result = await _try_repository_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                client=client,
            )
        elif method == "unpaywall":
            result = await _try_unpaywall_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                client=client,
            )
        elif method == "paper_fetch":
            result = await _engine_try_paper_fetch_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
            )
        elif method == "libgen":
            result = await _try_libgen_download_legacy(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                libgen_base_url=libgen_base_url,
            )
        else:
            result = {"method": method, "path": None, "error": f"Unknown download method '{method}'"}
        elapsed = time.perf_counter() - started
        ok = bool(result.get("path") and _is_valid_pdf_file(result.get("path")))
        if method != "paper_fetch":
            await asyncio.to_thread(
                record_download_health,
                method=method,
                source=source_name,
                ok=ok,
                elapsed_seconds=elapsed,
                error=str(result.get("error") or ""),
            )
        result["elapsed_seconds"] = round(elapsed, 3)
        return result
    except Exception as exc:
        elapsed = time.perf_counter() - started
        await asyncio.to_thread(
            record_download_health,
            method=method,
            source=source_name,
            ok=False,
            elapsed_seconds=elapsed,
            error=str(exc),
        )
        raise


async def _race_oa_downloads_legacy(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
    client: Optional[httpx.AsyncClient] = None,
    strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    methods = rank_download_methods(["primary", "repositories", "unpaywall"], source=source_name)
    if doi:
        methods = ["publisher_direct", *methods]
    libgen_allowed = _engine_libgen_enabled(use_libgen)
    strategy_name = _engine_download_strategy(strategy)
    if strategy_name == "sequential":
        methods = [*methods, "paper_fetch"]
        if libgen_allowed:
            methods.append("libgen")
        errors: List[str] = []
        for method in methods:
            try:
                result = await _attempt_download_method_legacy(
                    method=method,
                    source_name=source_name,
                    paper_id=paper_id,
                    doi=doi,
                    title=title,
                    save_path=save_path,
                    client=client,
                    libgen_base_url=libgen_base_url,
                )
            except Exception as exc:
                errors.append(f"{method}: {exc}")
                continue
            if result.get("path") and _is_valid_pdf_file(result.get("path")):
                return result, errors
            if result.get("error"):
                errors.append(f"{result.get('method', method)}: {result['error']}")
        return None, errors
    if strategy_name == "race" and libgen_allowed:
        methods.append("libgen")

    async def _attempt(method: str) -> Dict[str, Any]:
        return await _attempt_download_method_legacy(
            method=method,
            source_name=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            client=client,
            libgen_base_url=libgen_base_url,
        )

    tasks = {asyncio.create_task(_attempt(method)): method for method in methods}
    errors: List[str] = []
    try:
        for completed in asyncio.as_completed(tasks):
            try:
                result = await completed
            except Exception as exc:
                errors.append(str(exc))
                continue
            if _looks_like_pdf_path(result.get("path")):
                for task in tasks:
                    if not task.done():
                        task.cancel()
                return result, errors
            if result.get("error"):
                errors.append(f"{result.get('method', 'download')}: {result['error']}")
    finally:
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return None, errors


def _download_helper_context() -> Dict[str, Any]:
    repository_searchers = [
        (name, _SEARCHERS[name])
        for name in ("openaire", "core", "europepmc", "pmc")
        if _SEARCHERS.get(name) is not None
    ]
    return {
        "searchers": _SEARCHERS,
        "repository_searchers": repository_searchers or None,
        "unpaywall_resolver": unpaywall_resolver,
    }


async def _download_with_fallback_path(
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
) -> Any:
    save_path = resolve_save_path(save_path)
    source_name = source.strip().lower()
    source_name, routed_paper_id, routed_doi = _source_from_identifier(
        source_name,
        paper_id,
        doi,
    )
    paper_id, doi = routed_paper_id, routed_doi
    arxiv_id = _extract_arxiv_id(paper_id, doi, title)
    if arxiv_id:
        source_name, paper_id = "arxiv", arxiv_id

    result, errors = await _race_oa_downloads_legacy(
        source_name=source_name,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
        client=client,
        strategy=download_strategy,
        use_libgen=use_libgen,
        libgen_base_url=libgen_base_url,
    )
    if result and isinstance(result.get("path"), str):
        return result["path"]

    strategy_name = _engine_download_strategy(download_strategy)
    if strategy_name != "sequential":
        paper_fetch_result = await _engine_try_paper_fetch_download(
            source_name=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
        )
        if isinstance(paper_fetch_result.get("path"), str):
            return paper_fetch_result["path"]
        if paper_fetch_result.get("error"):
            errors.append(f"paper_fetch: {paper_fetch_result['error']}")
    if strategy_name == "oa_first" and _engine_libgen_enabled(use_libgen):
        libgen_result = await _try_libgen_download_legacy(
            source_name=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            libgen_base_url=libgen_base_url,
        )
        if isinstance(libgen_result.get("path"), str):
            return libgen_result["path"]
        if libgen_result.get("error"):
            errors.append(f"libgen: {libgen_result['error']}")

    if not use_scihub:
        return "Download failed after OA fallback chain. Details: " + " | ".join(errors)

    fetcher = SciHubFetcher(base_url=scihub_base_url, output_dir=save_path)
    identifier = (doi or "").strip() or (title or "").strip() or paper_id
    fallback = await asyncio.to_thread(fetcher.download_pdf, identifier)
    if fallback and os.path.exists(fallback):
        record_download(
            pdf_path=fallback,
            source=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="scihub",
            legal_status="user_opt_in_scihub",
        )
        return str(fallback)
    return "Sci-Hub fallback also failed."


async def _resolve_session_paper_pdf(
    *,
    paper: Dict[str, Any],
    index: int,
    save_path: str,
    use_scihub: bool,
) -> Dict[str, Any]:
    candidate = _paper_parse_candidate(paper, index)
    if candidate.get("parse_ready") and candidate.get("pdf_url"):
        local = str(candidate.get("local_pdf_path") or "").strip()
        if not (local and os.path.exists(local)):
            hint = str(
                candidate.get("canonical_pdf_stem")
                or candidate.get("paper_id")
                or candidate.get("doi")
                or candidate.get("title")
                or f"paper_{index}"
            )
            downloaded = await _download_from_url(candidate["pdf_url"], save_path, hint)
            if downloaded and os.path.exists(downloaded):
                record_download(
                    pdf_path=downloaded,
                    source=candidate.get("source", ""),
                    paper_id=candidate.get("paper_id", ""),
                    doi=candidate.get("doi", ""),
                    title=candidate.get("title", ""),
                    downloader="search_result_pdf_url",
                    legal_status="search_result_open_access_pdf_url",
                )
                return {
                    "index": index,
                    "status": "ready",
                    "candidate": candidate,
                    "download_method": "search_result_pdf_url",
                    "pdf_path": downloaded,
                }
    return await _engine_resolve_session_paper_pdf(
        paper=paper,
        index=index,
        save_path=save_path,
        use_scihub=use_scihub,
        **_download_helper_context(),
    )


async def _download_selected_session_paper(
    *,
    paper: Dict[str, Any],
    index: int,
    save_path: str,
    use_scihub: bool,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    candidate = _paper_parse_candidate(paper, index)
    if candidate.get("parse_ready"):
        existing_path, existing_method = _find_existing_pdf(
            candidate,
            index=index,
            save_path=save_path,
        )
        if existing_path:
            return {
                "index": index,
                "status": "skipped_existing",
                "candidate": candidate,
                "download_method": existing_method,
                **_pdf_result_metadata(existing_path),
            }
        if candidate.get("pdf_url"):
            hint = str(
                candidate.get("canonical_pdf_stem")
                or candidate.get("paper_id")
                or candidate.get("doi")
                or candidate.get("title")
                or f"paper_{index}"
            )
            try:
                downloaded = await _download_from_url(
                    candidate["pdf_url"],
                    save_path,
                    hint,
                    client=client,
                )
            except TypeError as exc:
                if "client" not in str(exc):
                    raise
                downloaded = await _download_from_url(
                    candidate["pdf_url"],
                    save_path,
                    hint,
                )
            if downloaded and os.path.exists(downloaded):
                if not _is_valid_pdf_file(downloaded):
                    return {
                        "index": index,
                        "status": "invalid_pdf",
                        "candidate": candidate,
                        "download_method": "search_result_pdf_url",
                        "pdf_path": downloaded,
                        "message": "Downloaded file failed PDF validation.",
                    }
                await asyncio.to_thread(
                    record_download,
                    pdf_path=downloaded,
                    source=candidate.get("source", ""),
                    paper_id=candidate.get("paper_id", ""),
                    doi=candidate.get("doi", ""),
                    title=candidate.get("title", ""),
                    downloader="search_result_pdf_url",
                    legal_status="search_result_open_access_pdf_url",
                )
                return {
                    "index": index,
                    "status": "downloaded",
                    "candidate": candidate,
                    "download_method": "search_result_pdf_url",
                    **_pdf_result_metadata(downloaded),
                }
    return await _engine_download_selected_session_paper(
        paper=paper,
        index=index,
        save_path=save_path,
        use_scihub=use_scihub,
        client=client,
        **_download_helper_context(),
    )


def _prefer_local_selection_surface(response: Dict[str, Any]) -> Dict[str, Any]:
    """Expose localhost fallback details from a nested parse prompt."""
    if not isinstance(response, dict):
        return response
    prompt = response.get("parse_prompt")
    if not isinstance(prompt, dict):
        return response
    local = prompt.get("local_browser")
    if not isinstance(local, dict):
        return response
    url = str(local.get("url") or "")
    if not url:
        return response
    surface = local.get("selection_surface")
    surface_name = surface.get("surface") if isinstance(surface, dict) else ""
    if surface_name == "hybrid":
        response.setdefault("interaction", "mcp_app")
        response.setdefault("recommended_tool", PAPER_SELECTION_WIDGET_TOOL)
    else:
        response["interaction"] = local.get("interaction", "local_browser_checkbox")
        response["recommended_tool"] = LOCAL_PAPER_SELECTION_TOOL
    response["recommended_url"] = url
    response["local_browser_url"] = url
    response["page_id"] = local.get("page_id", "")
    response["opened"] = bool(local.get("opened", False))
    response["local_browser"] = local
    if "parse_decision_required" in prompt:
        response["parse_decision_required"] = bool(prompt.get("parse_decision_required"))
    if "requires_user_parse_decision" in prompt:
        response["requires_user_parse_decision"] = bool(
            prompt.get("requires_user_parse_decision")
        )
    return response


async def _after_saved_pdf(result: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("after_save_prompt_hook", _prompt_parse_saved_pdfs)
    kwargs.setdefault("searchers", _SEARCHERS)
    kwargs.setdefault("_attach_local_selection_ui_fn", _attach_local_selection_ui)
    response = await _engine_after_saved_pdf(result, **kwargs)
    if isinstance(response, dict):
        response = _prefer_local_selection_surface(response)
    return (
        _promote_paper_selection_app(response)
        if isinstance(response, dict)
        and _should_promote_paper_selection_app(response.get("parse_prompt"))
        else response
    )


async def _after_saved_pdfs(pdf_paths: List[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
    kwargs.setdefault("after_save_prompt_hook", _prompt_parse_saved_pdfs)
    kwargs.setdefault("searchers", _SEARCHERS)
    kwargs.setdefault("_attach_local_selection_ui_fn", _attach_local_selection_ui)
    response = await _engine_after_saved_pdfs(pdf_paths, **kwargs)
    if isinstance(response, dict):
        response = _prefer_local_selection_surface(response)
    return (
        _promote_paper_selection_app(response)
        if isinstance(response, dict)
        and _should_promote_paper_selection_app(response.get("parse_prompt"))
        else response
    )


async def _download_source_pdf(
    searcher: Any,
    *,
    source: str,
    paper_id: str,
    save_path: str,
    ctx: Any = None,
    doi: str = "",
    title: str = "",
    downloader: str = "",
    legal_status: str = "source_native_or_open_access",
    parse_execution: str = "background",
    custom_save_path_confirmed: bool = False,
    after_save_hook: Any = None,
) -> Any:
    searchers = dict(_SEARCHERS)
    if searcher is not None:
        searchers[source.strip().lower()] = searcher
    return await _engine_download_source_pdf(
        searchers,
        source=source,
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
        doi=doi,
        title=title,
        downloader=downloader,
        legal_status=legal_status,
        parse_execution=parse_execution,
        custom_save_path_confirmed=custom_save_path_confirmed,
        after_save_hook=after_save_hook or _after_saved_pdf,
    )


async def _read_source_paper(
    searcher: Any,
    *,
    source: str,
    paper_id: str,
    save_path: str,
    ctx: Any = None,
    doi: str = "",
    title: str = "",
    custom_save_path_confirmed: bool = False,
) -> Any:
    searchers = dict(_SEARCHERS)
    searchers[(source or "").strip().lower()] = searcher
    return await _engine_read_source_paper(
        searchers,
        source=source,
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
        doi=doi,
        title=title,
        custom_save_path_confirmed=custom_save_path_confirmed,
        after_save_hook=_after_saved_pdf,
    )


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
    result = await submit_parse_job(
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
        result.setdefault("selection_semantics", SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE)
    return result


async def _prompt_parse_saved_pdfs(**kwargs: Any) -> Dict[str, Any]:
    """Server-layer adapter that injects MCP tool callbacks into parse engine."""
    kwargs.setdefault("_parse_selected_papers_fn", parse_selected_papers)
    kwargs.setdefault("_submit_parse_job_fn", submit_parse_job)
    kwargs.setdefault("_attach_local_selection_ui_fn", _attach_local_selection_ui)
    return await _engine_prompt_parse_saved_pdfs(**kwargs)


async def _parse_prompt_for_download_results(**kwargs: Any) -> Dict[str, Any]:
    """Server-layer adapter that injects MCP tool callbacks into parse engine."""
    kwargs.setdefault("_parse_selected_papers_fn", parse_selected_papers)
    kwargs.setdefault("_submit_parse_job_fn", submit_parse_job)
    kwargs.setdefault("_attach_local_selection_ui_fn", _attach_local_selection_ui)
    return await _engine_parse_prompt_for_download_results(**kwargs)


async def _pre_download_selection_prompt(**kwargs: Any) -> Dict[str, Any]:
    """Server-layer adapter that attaches the local checkbox fallback."""
    kwargs.setdefault("_attach_local_selection_ui_fn", _attach_local_selection_ui)
    return await _engine_pre_download_selection_prompt(**kwargs)


async def _download_and_parse_session_paper(
    paper: Dict[str, Any],
    index: int,
    save_path: str,
    use_scihub: bool,
    mode: str,
    backend: str,
    force: bool,
) -> Dict[str, Any]:
    resolved = await _resolve_session_paper_pdf(
        paper=paper,
        index=index,
        save_path=save_path,
        use_scihub=use_scihub,
    )
    if resolved.get("status") != "ready":
        return resolved

    candidate = resolved["candidate"]
    parse_result = await parse_pdf_with_mineru(
        pdf_path=resolved["pdf_path"],
        source=candidate["source"],
        paper_id=candidate["paper_id"],
        doi=candidate["doi"],
        title=candidate["title"],
        mode=mode,
        backend=backend,
        force=force,
    )
    return {
        "index": index,
        "status": parse_result.get("status", "unknown"),
        "candidate": candidate,
        "download_method": resolved["download_method"],
        "pdf_path": resolved["pdf_path"],
        "parse": parse_result,
    }


def _workflow_intent_name(intent: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (intent or "").strip().lower()).strip("_")
    return normalized or "search_download_parse"


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

    mode = re.sub(r"[^a-z0-9]+", "_", (selection_mode or "").strip().lower()).strip("_") or "auto_top"
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
# Optional IEEE Xplore tools — registered only when API key is set
# ---------------------------------------------------------------------------
if ieee_searcher is not None:
    @mcp.tool()
    async def search_ieee(query: str, max_results: int = 10) -> List[Dict]:
        """Search IEEE Xplore for papers.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY).

        Args:
            query: Search query string.
            max_results: Maximum number of results (default: 10).
        Returns:
            List of paper dicts from IEEE Xplore.
        """
        return await async_search(ieee_searcher, query, max_results)

    @mcp.tool()
    async def download_ieee(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download a PDF from IEEE Xplore.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY) and institutional access.

        Args:
            paper_id: IEEE Xplore paper identifier.
            save_path: Directory to save the PDF (default: '~/Desktop/papers').
        Returns:
            str: Path to saved PDF or error message.
        """
        return await _download_source_pdf(
            ieee_searcher,
            source="ieee",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )

    @mcp.tool()
    async def read_ieee_paper(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download and read an IEEE Xplore paper.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY).

        Args:
            paper_id: IEEE Xplore paper identifier.
            save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
        Returns:
            str: Extracted text content.
        """
        return await _read_source_paper(
            ieee_searcher,
            source="ieee",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )


# ---------------------------------------------------------------------------
# Optional ACM Digital Library tools — registered only when API key is set
# ---------------------------------------------------------------------------
if acm_searcher is not None:
    @mcp.tool()
    async def search_acm(query: str, max_results: int = 10) -> List[Dict]:
        """Search ACM Digital Library for papers.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY).

        Args:
            query: Search query string.
            max_results: Maximum number of results (default: 10).
        Returns:
            List of paper dicts from ACM DL.
        """
        return await async_search(acm_searcher, query, max_results)

    @mcp.tool()
    async def download_acm(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download a PDF from ACM Digital Library.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY) and institutional access.

        Args:
            paper_id: ACM DL paper identifier.
            save_path: Directory to save the PDF (default: '~/Desktop/papers').
        Returns:
            str: Path to saved PDF or error message.
        """
        return await _download_source_pdf(
            acm_searcher,
            source="acm",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )

    @mcp.tool()
    async def read_acm_paper(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download and read an ACM Digital Library paper.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY).

        Args:
            paper_id: ACM DL paper identifier.
            save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
        Returns:
            str: Extracted text content.
        """
        return await _read_source_paper(
            acm_searcher,
            source="acm",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )


def main():
    # Start idle-timeout monitor (daemon thread, shared across all transports).
    _monitor = threading.Thread(target=_idle_timeout_monitor, daemon=True, name="idle-timeout")
    _monitor.start()

    # Wrap mcp.call_tool so every tool invocation refreshes the idle clock.
    _original_call_tool = mcp.call_tool

    async def _tracked_call_tool(name: str, arguments: dict) -> Any:
        _update_activity()
        return await _original_call_tool(name, arguments)

    mcp.call_tool = _tracked_call_tool  # type: ignore[method-assign]

    transport = get_env("TRANSPORT", "stdio").strip().lower() or "stdio"
    if transport in {"http", "streamable_http", "streamable-http"}:
        _configure_http_transport_from_env()
        logger.info(
            "Starting paper-search MCP over streamable HTTP at http://%s:%s%s",
            mcp.settings.host,
            mcp.settings.port,
            mcp.settings.streamable_http_path,
        )
        mcp.run(transport="streamable-http")
        return
    if transport == "sse":
        _configure_http_transport_from_env()
        logger.info("Starting paper-search MCP over SSE at http://%s:%s/sse", mcp.settings.host, mcp.settings.port)
        mcp.run(transport="sse")
        return
    if transport != "stdio":
        logger.warning("Unknown PAPER_SEARCH_MCP_TRANSPORT=%r; falling back to stdio", transport)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

