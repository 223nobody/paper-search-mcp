# paper_search_mcp/engine/parse.py
"""
Parse decision logic, batch prompts, MinerU key management, and download prompts.

Extracted from server.py.  No MCP / FastMCP dependencies.  Functions that call
MCP tools (parse_selected_papers, submit_parse_job) accept optional callable
overrides with lazy-import fallbacks to avoid circular imports.

Paper-metadata helpers that are shared across modules live in ``.paper`` and are
imported from there.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..cache import (
    create_search_session as cache_create_search_session,
    get_search_session as cache_get_search_session,
    read_parse_prompt_state as cache_read_parse_prompt_state,
    read_json,
    short_hash,
    utc_now,
    write_json,
    write_parse_prompt_state as cache_write_parse_prompt_state,
    _session_path as cache_session_path,
)
from ..config import env_file_path, get_env
from ..utils import DEFAULT_SAVE_PATH, extract_doi, resolve_save_path
from .paper import (
    _paper_parse_candidate,
    _ranking_profile_name,
)
from .search import SOURCE_CAPABILITIES, _env_int, _env_float

logger = logging.getLogger(__name__)

# ===========================================================================
# Constants (mirrors server.py)
# ===========================================================================

ALLOW_CUSTOM_SAVE_PATH_ENV = "ALLOW_CUSTOM_SAVE_PATH"
REQUIRE_EXPLICIT_SAVE_PATH_ENV = "REQUIRE_EXPLICIT_SAVE_PATH"
SEARCH_SOURCE_TIMEOUT_ENV = "SEARCH_SOURCE_TIMEOUT_SECONDS"
DOWNLOAD_TIMEOUT_ENV = "DOWNLOAD_TIMEOUT_SECONDS"
AUTO_OPEN_SELECTION_UI_ENV = "AUTO_OPEN_SELECTION_UI"
SELECTION_UI_MODE_ENV = "SELECTION_UI_MODE"
SAVED_PDF_BATCH_PROMPT_ENV = "SAVED_PDF_BATCH_PROMPT"
SAVED_PDF_BATCH_WINDOW_ENV = "SAVED_PDF_BATCH_WINDOW_SECONDS"
PARSE_PROMPT_TIMEOUT_SECONDS_ENV = "PARSE_PROMPT_TIMEOUT_SECONDS"
PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS_ENV = "PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS"
PARSE_PROMPT_TIMEOUT_ACTION_ENV = "PARSE_PROMPT_TIMEOUT_ACTION"
PARSE_PROMPT_ALLOW_REOPEN_ENV = "PARSE_PROMPT_ALLOW_REOPEN"

_DEFAULT_PARSE_PROMPT_TIMEOUT_SECONDS = 180
_DEFAULT_PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS = 15

PAPER_SELECTION_WIDGET_URI = "ui://paper-search/paper-selection.html"
PAPER_SELECTION_WIDGET_TOOL = "render_paper_selection_app"
MINERU_KEY_WIDGET_URI = "ui://paper-search/mineru-api-key.html"
MINERU_KEY_WIDGET_TOOL = "render_mineru_api_key_setup_app"
MINERU_KEY_CONFIG_TOOL = "configure_mineru_api_key"

AUTO_PARSE_SAVED_PDF_LIMIT = 10

SELECTION_SEMANTICS_PARSE = "parse_selected"
SELECTION_SEMANTICS_DOWNLOAD_ONLY = "download_selected_only"
SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE = "download_and_parse_selected_only"

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

AGENT_SKILL_RANKING_PROFILE = "agent-skill"
AGENT_SKILL_PROFILE_ALIASES = {AGENT_SKILL_RANKING_PROFILE, "agent_skill", "agentskill", "skill-agent"}

GENERIC_PUBLICATION_VENUES = {
    "arxiv",
    "arxiv.org",
    "arxiv preprint",
    "arxiv preprints",
    "preprint",
    "preprints",
}

ARXIV_CATEGORY_VENUES = {
    "cs.AI": "Artificial Intelligence",
    "cs.AR": "Hardware Architecture",
    "cs.CC": "Computational Complexity",
    "cs.CE": "Computational Engineering, Finance, and Science",
    "cs.CG": "Computational Geometry",
    "cs.CL": "Computation and Language",
    "cs.CR": "Cryptography and Security",
    "cs.CV": "Computer Vision and Pattern Recognition",
    "cs.CY": "Computers and Society",
    "cs.DB": "Databases",
    "cs.DC": "Distributed, Parallel, and Cluster Computing",
    "cs.DL": "Digital Libraries",
    "cs.DM": "Discrete Mathematics",
    "cs.DS": "Data Structures and Algorithms",
    "cs.ET": "Emerging Technologies",
    "cs.FL": "Formal Languages and Automata Theory",
    "cs.GL": "General Literature",
    "cs.GR": "Graphics",
    "cs.GT": "Computer Science and Game Theory",
    "cs.HC": "Human-Computer Interaction",
    "cs.IR": "Information Retrieval",
    "cs.IT": "Information Theory",
    "cs.LG": "Machine Learning",
    "cs.LO": "Logic in Computer Science",
    "cs.MA": "Multiagent Systems",
    "cs.MM": "Multimedia",
    "cs.MS": "Mathematical Software",
    "cs.NA": "Numerical Analysis",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.NI": "Networking and Internet Architecture",
    "cs.OH": "Other Computer Science",
    "cs.OS": "Operating Systems",
    "cs.PF": "Performance",
    "cs.PL": "Programming Languages",
    "cs.RO": "Robotics",
    "cs.SC": "Symbolic Computation",
    "cs.SD": "Sound",
    "cs.SE": "Software Engineering",
    "cs.SI": "Social and Information Networks",
    "cs.SY": "Systems and Control",
    "stat.ML": "Machine Learning",
    "eess.IV": "Image and Video Processing",
}


# ===========================================================================
# Environment helpers (local copies)
# ===========================================================================

def _env_flag_enabled(name: str, default: str = "false") -> bool:
    value = get_env(name, default).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _parse_prompt_timeout_seconds() -> int:
    return _env_int(
        PARSE_PROMPT_TIMEOUT_SECONDS_ENV,
        _DEFAULT_PARSE_PROMPT_TIMEOUT_SECONDS,
        minimum=1,
    )


def _parse_prompt_timeout_per_paper_seconds() -> int:
    return _env_int(
        PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS_ENV,
        _DEFAULT_PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS,
        minimum=0,
    )


def _compute_dynamic_parse_prompt_timeout(num_papers: int = 0) -> int:
    """Compute parse-prompt timeout scaled by paper count.

    Formula: max(base_timeout, num_papers × per_paper_seconds)

    This gives the user more time to review larger result sets before the
    selection prompt times out.  The base (env-configurable) floor prevents
    the timeout from ever dropping below a reasonable minimum.
    """
    base = _parse_prompt_timeout_seconds()
    per_paper = _parse_prompt_timeout_per_paper_seconds()
    if per_paper <= 0 or num_papers <= 0:
        return base
    return max(base, num_papers * per_paper)


def _parse_prompt_timeout_action() -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        get_env(PARSE_PROMPT_TIMEOUT_ACTION_ENV, "no_parse").strip().lower(),
    ).strip("_")
    return "no_parse" if normalized in {"", "none", "skip"} else normalized


def _parse_prompt_allow_reopen() -> bool:
    return _env_flag_enabled(PARSE_PROMPT_ALLOW_REOPEN_ENV, default="false")


def _parse_prompt_expires_at(timeout_seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=max(1, int(timeout_seconds)))
    ).isoformat()


def _parse_prompt_terminal_message(
    state: str,
    timeout_seconds: int,
    reason: str = "",
) -> str:
    if state == PARSE_PROMPT_STATE_TIMED_OUT_NO_PARSE or reason == "timeout":
        return PARSE_PROMPT_TIMEOUT_MESSAGE.format(
            timeout_seconds=max(1, int(timeout_seconds or 120))
        )
    return (
        "PDFs were saved. MinerU parsing was not started because the optional "
        "parse prompt was dismissed. To parse later, run an explicit parse "
        "command for the saved PDFs."
    )


def _parse_prompt_id(download_selection_token: str, created_at: str) -> str:
    digest = short_hash(f"{download_selection_token}|{created_at}", 10)
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", download_selection_token).strip("._")
    return f"parse_prompt_{safe or 'download'}_{digest}"


def _parse_prompt_timeout_metadata(
    *,
    download_selection_token: str,
    parse_selection_token: str,
    prompt_id: str = "",
    num_papers: int = 0,
) -> Dict[str, Any]:
    timeout_seconds = _compute_dynamic_parse_prompt_timeout(num_papers)
    created_at = utc_now()
    effective_prompt_id = prompt_id or _parse_prompt_id(
        download_selection_token, created_at
    )
    timeout_action = _parse_prompt_timeout_action()
    return {
        "prompt_id": effective_prompt_id,
        "prompt_state": PARSE_PROMPT_STATE_PENDING,
        "created_at": created_at,
        "expires_at": _parse_prompt_expires_at(timeout_seconds),
        "timeout_seconds": timeout_seconds,
        "timeout_action": timeout_action,
        "terminal_on_timeout": True,
        "allow_reopen": _parse_prompt_allow_reopen(),
        "timeout_message": PARSE_PROMPT_TIMEOUT_MESSAGE.format(
            timeout_seconds=timeout_seconds
        ),
        "download_selection_token": download_selection_token,
        "parse_selection_token": parse_selection_token,
    }


def _parse_prompt_terminal_response(state: Dict[str, Any]) -> Dict[str, Any]:
    status = str(state.get("state") or PARSE_PROMPT_STATE_TIMED_OUT_NO_PARSE)
    timeout_seconds = int(state.get("timeout_seconds") or _parse_prompt_timeout_seconds())
    message = str(state.get("message") or "") or _parse_prompt_terminal_message(
        status,
        timeout_seconds,
        reason=str(state.get("reason") or ""),
    )
    return {
        "status": status,
        "interaction": "download_selected_papers_parse_prompt",
        "selection_token": str(state.get("download_selection_token") or ""),
        "download_selection_token": str(state.get("download_selection_token") or ""),
        "parse_selection_token": str(state.get("parse_selection_token") or ""),
        "prompt_id": str(state.get("prompt_id") or ""),
        "prompt_state": status,
        "state": status,
        "reason": str(state.get("reason") or ""),
        "message": message,
        "parse_execution": "none",
        "parse_decision_required": False,
        "requires_user_parse_decision": False,
        "recommended_tool": "",
        "recommended_selected_indices": "",
        "default_parse_selected_indices": "",
        "terminal": True,
        "timeout_seconds": timeout_seconds,
        "timeout_action": str(state.get("timeout_action") or "no_parse"),
        "allow_reopen": False,
        "updated_at": str(state.get("updated_at") or ""),
    }


def _terminal_parse_prompt_for_download(selection_token: str) -> Dict[str, Any]:
    state = cache_read_parse_prompt_state(selection_token)
    if not isinstance(state, dict):
        return {}
    if str(state.get("state") or "") not in TERMINAL_PARSE_PROMPT_STATES:
        return {}
    return _parse_prompt_terminal_response(state)


def _write_pending_parse_prompt_state(prompt: Dict[str, Any]) -> Dict[str, Any]:
    download_selection_token = str(prompt.get("download_selection_token") or "")
    if not download_selection_token:
        return {}
    state = {
        "download_selection_token": download_selection_token,
        "parse_selection_token": str(prompt.get("selection_token") or ""),
        "prompt_id": str(prompt.get("prompt_id") or ""),
        "state": PARSE_PROMPT_STATE_PENDING,
        "reason": "",
        "timeout_seconds": int(
            prompt.get("timeout_seconds") or _parse_prompt_timeout_seconds()
        ),
        "timeout_action": str(prompt.get("timeout_action") or "no_parse"),
        "expires_at": str(prompt.get("expires_at") or ""),
        "allow_reopen": bool(prompt.get("allow_reopen", False)),
        "message": str(prompt.get("message") or ""),
    }
    return cache_write_parse_prompt_state(download_selection_token, state)


def dismiss_parse_prompt_state(
    selection_token: str,
    *,
    prompt_id: str = "",
    reason: str = "timeout",
) -> Dict[str, Any]:
    """Mark a download-after-parse prompt terminal without starting MinerU."""
    download_selection_token = str(selection_token or "").strip()
    if not download_selection_token:
        return {
            "status": "invalid_selection",
            "selection_token": "",
            "message": "selection_token is required.",
            "terminal": True,
        }

    existing = cache_read_parse_prompt_state(download_selection_token)
    if isinstance(existing, dict) and str(existing.get("state") or "") in TERMINAL_PARSE_PROMPT_STATES:
        return _parse_prompt_terminal_response(existing)

    normalized_reason = re.sub(
        r"[^a-z0-9]+", "_", (reason or "timeout").strip().lower()
    ).strip("_") or "timeout"
    state_name = (
        PARSE_PROMPT_STATE_TIMED_OUT_NO_PARSE
        if normalized_reason in {"timeout", "idle_timeout", "timed_out"}
        else PARSE_PROMPT_STATE_COMPLETED_NO_PARSE
    )

    if not isinstance(existing, dict):
        existing = {}
    timeout_seconds = int(
        existing.get("timeout_seconds") or _parse_prompt_timeout_seconds()
    )
    message = _parse_prompt_terminal_message(
        state_name,
        timeout_seconds,
        reason=normalized_reason,
    )
    parse_selection_token = str(existing.get("parse_selection_token") or "")
    if not parse_selection_token:
        session = cache_get_search_session(download_selection_token)
        if isinstance(session, dict):
            parse_selection_token = str(
                session.get("metadata", {}).get("parse_selection_token") or ""
            )

    updated = cache_write_parse_prompt_state(
        download_selection_token,
        {
            **existing,
            "download_selection_token": download_selection_token,
            "parse_selection_token": parse_selection_token,
            "prompt_id": str(prompt_id or existing.get("prompt_id") or ""),
            "state": state_name,
            "reason": normalized_reason,
            "timeout_seconds": timeout_seconds,
            "timeout_action": str(existing.get("timeout_action") or "no_parse"),
            "allow_reopen": False,
            "message": message,
        },
    )
    return _parse_prompt_terminal_response(updated)


# ===========================================================================
# Name / semantics normalisation
# ===========================================================================

def _selection_semantics_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    if normalized in {
        "download", "download_selected", "download_only",
        "download_selected_only", "search_download", "search_download_only",
    }:
        return SELECTION_SEMANTICS_DOWNLOAD_ONLY
    if normalized in {
        "download_and_parse", "download_parse",
        "download_selected_and_parse", "download_and_parse_selected_only",
        "download_parse_selected_only", "search_download_parse",
    }:
        return SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE
    return SELECTION_SEMANTICS_PARSE


def _workflow_parse_execution_name(parse_execution: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+", "_", (parse_execution or "").strip().lower()
    ).strip("_")
    if normalized in {"sync", "synchronous", "wait", "blocking", "inline"}:
        return "sync"
    if normalized in {
        "prompt", "manual", "ask", "select", "selection", "checkbox",
        "choose", "user_select", "user_selection",
    }:
        return "prompt"
    if normalized in {
        "none", "no", "off", "false", "skip", "disable", "disabled",
        "never", "bypass",
    }:
        return "none"
    if normalized in {"background", "bg", "back", "bg_job", "job", "async"}:
        return "background"
    return "background"


# ===========================================================================
# MinerU API key helpers
# ===========================================================================

def _mineru_api_key_configured() -> bool:
    return bool(get_env("MINERU_API_KEY", "").strip())


def _mineru_batch_parse_enabled(mode: str = "auto") -> bool:
    normalized_mode = (mode or "auto").strip().lower()
    if normalized_mode not in {"auto", "extract", "cloud_api"}:
        return False
    if not _mineru_api_key_configured():
        return False
    if normalized_mode in {"extract", "cloud_api"}:
        return True

    if not _env_flag_enabled("MINERU_BATCH_PARSE", default="false"):
        return False

    configured = get_env("MINERU_AUTO_ORDER", "").strip().lower()
    if configured:
        first = next(
            (part.strip() for part in configured.split(",") if part.strip()), ""
        )
        return first in {"extract", "cloud_api"}
    return True


def _mineru_key_app_meta() -> Dict[str, Any]:
    return {
        "tool": MINERU_KEY_WIDGET_TOOL,
        "resource_uri": MINERU_KEY_WIDGET_URI,
        "output_template": MINERU_KEY_WIDGET_URI,
        "widget_accessible": True,
        "ui": {
            "resourceUri": MINERU_KEY_WIDGET_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": MINERU_KEY_WIDGET_URI,
        "openai/widgetAccessible": True,
    }


def _mineru_key_setup_prompt(
    reason: str = "missing", message: str = ""
) -> Dict[str, Any]:
    detail = message or (
        "MinerU official extract parsing needs PAPER_SEARCH_MCP_MINERU_API_KEY. "
        f"Open {MINERU_KEY_WIDGET_TOOL} to enter and save the key."
    )
    return {
        "status": "mineru_api_key_required",
        "interaction": "mcp_app",
        "reason": reason,
        "message": detail,
        "render_tool": MINERU_KEY_WIDGET_TOOL,
        "resource_uri": MINERU_KEY_WIDGET_URI,
        "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
        "env_file_path": str(env_file_path()),
        "_meta": _mineru_key_app_meta(),
    }


def _is_mineru_api_key_error(message: str) -> bool:
    lowered = message.lower()
    needles = (
        "mineru_api_key",
        "api_key",
        "api key",
        "authorization",
        "unauthorized",
        "forbidden",
        "401",
        "403",
        "invalid token",
        "token expired",
        "permission",
    )
    return any(needle in lowered for needle in needles)


def _mineru_api_key_prompt_for_parse_result(
    parse_result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if parse_result.get("status") not in {"error", "invalid_save_path"}:
        return None
    message = str(parse_result.get("message") or "")
    if not _mineru_api_key_configured():
        return _mineru_key_setup_prompt(
            "missing",
            "MinerU API key is not configured. Enter it to enable extract parsing.",
        )
    if _is_mineru_api_key_error(message):
        return _mineru_key_setup_prompt(
            "expired_or_invalid",
            "MinerU API key appears invalid or expired. Enter a new key.",
        )
    return None


def _attach_mineru_key_prompt(parse_result: Dict[str, Any]) -> Dict[str, Any]:
    prompt = _mineru_api_key_prompt_for_parse_result(parse_result)
    if prompt:
        parse_result = {**parse_result, "mineru_api_key_prompt": prompt}
    return parse_result


def _first_mineru_key_prompt(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        prompt = value.get("mineru_api_key_prompt")
        if isinstance(prompt, dict):
            return prompt
        for child in value.values():
            found = _first_mineru_key_prompt(child)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_mineru_key_prompt(item)
            if found:
                return found
    return None


# ===========================================================================
# Selection UI config helpers
# ===========================================================================

def _auto_open_selection_ui_enabled() -> bool:
    return _selection_ui_should_open(force_open=False)


def _selection_ui_mode() -> str:
    raw = get_env(SELECTION_UI_MODE_ENV, "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if normalized in {"off", "none", "disabled", "disable", "false", "no"}:
        return "off"
    if normalized in {"app", "app_only", "mcp_app", "mcp"}:
        return "app_only"
    if normalized in {
        "browser", "local", "local_browser", "open", "force", "force_open",
    }:
        return "local_browser"
    # ── Auto-detect: prefer MCP Apps widget when the host supports it ──
    if not raw or normalized == "auto":
        from ..utils import host_supports_mcp_apps_widget  # noqa: PLC0415
        if host_supports_mcp_apps_widget():
            return "app_only"
    return "auto"


def _selection_ui_should_open(*, force_open: bool = False) -> bool:
    mode = _selection_ui_mode()
    if mode == "off":
        return False
    from ..utils import host_supports_mcp_apps_widget  # noqa: PLC0415
    if host_supports_mcp_apps_widget():
        return False
    if mode == "app_only":
        return False
    if mode == "local_browser":
        return True
    if force_open:
        return True
    return _env_flag_enabled(AUTO_OPEN_SELECTION_UI_ENV, default="false")


def _selection_surface_policy(*, force_open: bool = False) -> Dict[str, Any]:
    """Describe the preferred paper-selection surface for the current host.

    Four primary client scenarios are explicitly mapped:

    ========================= ============= ============
    Scenario                   MCP App       Local Brwsr
    ========================= ============= ============
    Codex Desktop              ✅ (default)  ❌
    Codex VS Code plugin       ❌            ✅ (default)
    Claude Code Desktop        ✅ (default)  ❌
    Claude Code VS Code ext.   ❌            ✅ (default)
    ========================= ============= ============

    The ``SELECTION_UI_MODE`` env var can override: ``off``, ``app_only``,
    ``local_browser``, ``auto`` (default).
    """
    from ..utils import detect_host, host_supports_mcp_apps_widget  # noqa: PLC0415

    host = detect_host()
    mode = _selection_ui_mode()
    app_supported = host_supports_mcp_apps_widget()
    local_should_open = _selection_ui_should_open(force_open=force_open)

    # ── Per-host default surface (before mode overrides) ──
    # codex_vscode and claude_code_vscode do NOT have sandboxed iframe
    # support → fall back to localhost browser checkbox.
    _HOST_DEFAULT_SURFACE: Dict[str, str] = {
        "codex":                 "mcp_app",
        "claude_code_desktop":   "mcp_app",
        "claude_desktop":        "mcp_app",
        "codex_vscode":          "local_browser",
        "claude_code_vscode":    "local_browser",
        "claude_code_cli":       "local_browser",
        "vscode_generic":        "local_browser",
    }

    if mode == "off":
        surface = "numbered_fallback"
        reason = "selection_ui_disabled"
    elif mode == "app_only":
        surface = "mcp_app" if app_supported else "numbered_fallback"
        reason = "app_only_configured" if app_supported else "mcp_app_requested_but_unsupported"
    elif mode == "local_browser":
        surface = "local_browser"
        reason = "local_browser_configured"
    elif app_supported:
        surface = "mcp_app"
        reason = f"host_{host}_supports_mcp_app_sandbox"
    elif force_open or local_should_open:
        surface = "local_browser"
        reason = f"host_{host}_without_mcp_app_sandbox"
    else:
        surface = _HOST_DEFAULT_SURFACE.get(host, "numbered_fallback")
        reason = "local_browser_not_auto_opened"

    return {
        "surface": surface,
        "reason": reason,
        "detected_host": host,
        "ui_mode": mode,
        "app_widget_supported": app_supported,
        "local_browser_should_open": local_should_open,
    }


# ===========================================================================
# Selection parsing / UI label helpers
# ===========================================================================

def _parse_selected_indices(selected_indices: Any, max_index: int) -> List[int]:
    if max_index <= 0:
        return []

    raw_items: List[Any] = []
    if isinstance(selected_indices, str):
        value = selected_indices.strip().lower()
        if value in {"all", "*"}:
            return list(range(1, max_index + 1))
        raw_items = [part for part in re.split(r"[,\s]+", value) if part]
    elif isinstance(selected_indices, int):
        raw_items = [selected_indices]
    elif isinstance(selected_indices, (list, tuple, set)):
        raw_items = list(selected_indices)
    else:
        raise ValueError(
            "selected_indices must be 'all', a comma-separated string, "
            "or a list of numbers"
        )

    selected: List[int] = []
    for item in raw_items:
        if isinstance(item, str) and re.fullmatch(r"\d+\s*-\s*\d+", item):
            start_s, end_s = re.split(r"\s*-\s*", item, maxsplit=1)
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            selected.extend(range(start, end + 1))
            continue

        try:
            selected.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid selection index: {item}") from exc

    deduped: List[int] = []
    for index in selected:
        if index < 1 or index > max_index:
            raise ValueError(f"Selection index {index} is outside 1..{max_index}")
        if index not in deduped:
            deduped.append(index)

    if not deduped:
        raise ValueError("No selected indices provided")
    return deduped


def _shorten_for_option(value: str, limit: int = 120) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def _elicitation_option_label(candidate: Dict[str, Any]) -> str:
    index = candidate.get("index", "")
    title = _shorten_for_option(
        str(candidate.get("title") or "Untitled paper"), 140
    )
    source = str(candidate.get("source") or "unknown")
    year = str(candidate.get("year") or "n.d.")
    doi = str(candidate.get("doi") or "")
    paper_id = str(candidate.get("paper_id") or "")
    identifier = doi or paper_id or str(candidate.get("url") or "")
    suffix = f"{source}, {year}"
    if identifier:
        if len(identifier) > 80:
            identifier = identifier[:79].rstrip() + "..."
        suffix = f"{identifier}{' | ' + source if source else ''}, {year}"
    label = f"{index}. {title} [{suffix}]"
    return label[:200]


# ===========================================================================
# Elicitation schema / numbered fallback
# ===========================================================================

def _build_paper_selection_schema(options: List[str]) -> type:
    from pydantic import Field, create_model  # noqa: PLC0415

    return create_model(
        "PaperSelectionElicitation",
        selected_papers=(
            List[str],
            Field(
                default_factory=list,
                title="Papers to parse",
                description="Select one or more papers for MinerU PDF parsing.",
                json_schema_extra={
                    "items": {"type": "string", "enum": options},
                    "uniqueItems": True,
                },
            ),
        ),
    )


def _parse_elicitation_selected_indices(
    selected_values: Any, max_index: int
) -> List[int]:
    if selected_values is None:
        return []
    if isinstance(selected_values, str):
        selected_values = [selected_values]

    indices: List[int] = []
    for value in selected_values:
        text = str(value).strip()
        match = re.match(r"^(\d+)(?:[.\s]|$)", text)
        if match:
            indices.append(int(match.group(1)))
            continue
        if text.isdigit():
            indices.append(int(text))
            continue
        raise ValueError(f"Unable to parse selected paper option: {text}")

    return _parse_selected_indices(indices, max_index) if indices else []


def _numbered_paper_fallback(candidates: List[Dict[str, Any]]) -> List[str]:
    return [_elicitation_option_label(candidate) for candidate in candidates]


def _requested_selection_indices(requested_count: int, total: int) -> str:
    if requested_count <= 0 or total <= 0:
        return ""
    end = min(int(requested_count), int(total))
    if end <= 0:
        return ""
    return f"1-{end}" if end > 1 else "1"


def _codex_app_display_enabled() -> bool:
    """Return True when the current host can render the MCP Apps widget."""
    try:
        from ..utils import host_is_codex  # noqa: PLC0415
        return host_is_codex()
    except Exception:
        return False


def _format_selected_indices(indices: List[int]) -> str:
    """Format session indices as compact ranges without changing index meaning."""
    normalized = sorted({int(index) for index in indices if int(index) > 0})
    if not normalized:
        return ""

    ranges: List[str] = []
    start = prev = normalized[0]
    for index in normalized[1:]:
        if index == prev + 1:
            prev = index
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = index
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def _recommended_display_candidates(
    candidates: List[Dict[str, Any]],
    requested_count: int = 0,
) -> List[Dict[str, Any]]:
    """Return the default selection UI window while preserving session indices.

    Saved sessions can contain many ranked candidates.  User-facing selectors
    should show the request-sized shortlist, preferring candidates with a
    verified parse/download route, while download tools still receive the
    original session indices embedded in each candidate.

    Papers without a verified download route (``download_ready`` is ``False``)
    are excluded from the display window — users should only see papers that
    can actually be downloaded.
    """
    # ── Pre-filter: only candidates with a download route ──────────────
    _filtered = [
        c for c in candidates
        if c.get("download_ready") is not False
    ]

    if requested_count <= 0 or requested_count >= len(_filtered):
        return list(_filtered)

    selected: List[Dict[str, Any]] = []
    selected_indices: set[int] = set()

    def _append(candidate: Dict[str, Any]) -> None:
        index = int(candidate.get("index") or 0)
        if index <= 0 or index in selected_indices:
            return
        selected.append(candidate)
        selected_indices.add(index)

    # Phase 1 — fully verified routes first
    for candidate in _filtered:
        if len(selected) >= requested_count:
            break
        if (
            candidate.get("download_ready") is not False
            and candidate.get("parse_ready") is not False
        ):
            _append(candidate)

    # Phase 2 — remaining download-ready candidates to meet requested count
    for candidate in _filtered:
        if len(selected) >= requested_count:
            break
        _append(candidate)

    return selected


def _recommended_display_indices(
    candidates: List[Dict[str, Any]],
    requested_count: int = 0,
) -> str:
    return _format_selected_indices(
        [
            int(candidate.get("index") or 0)
            for candidate in _recommended_display_candidates(
                candidates,
                requested_count=requested_count,
            )
        ]
    )


def _codex_app_display_candidates(
    candidates: List[Dict[str, Any]],
    requested_count: int = 0,
) -> List[Dict[str, Any]]:
    return _recommended_display_candidates(
        candidates,
        requested_count=requested_count,
    )


def _reindexed_display_candidates(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return display candidates numbered from 1 while preserving source ranks."""
    reindexed: List[Dict[str, Any]] = []
    for display_index, candidate in enumerate(candidates, start=1):
        item = dict(candidate)
        try:
            source_index = int(
                item.get("source_index") or item.get("index") or display_index
            )
        except (TypeError, ValueError):
            source_index = display_index
        item.setdefault("source_index", source_index)
        item.setdefault("original_index", source_index)
        item["index"] = display_index
        reindexed.append(item)
    return reindexed


def _codex_recommended_selected_indices(
    candidates: List[Dict[str, Any]],
    requested_count: int = 0,
    fallback_total: int = 0,
) -> str:
    recommended = _recommended_display_indices(
        candidates,
        requested_count=requested_count,
    )
    if recommended:
        return recommended
    return _requested_selection_indices(requested_count, fallback_total or len(candidates))


# ===========================================================================
# Paper selection app meta / prompt / promotion
# ===========================================================================

def _paper_selection_app_meta() -> Dict[str, Any]:
    return {
        "tool": PAPER_SELECTION_WIDGET_TOOL,
        "resource_uri": PAPER_SELECTION_WIDGET_URI,
        "output_template": PAPER_SELECTION_WIDGET_URI,
        "widget_accessible": True,
        "ui": {
            "resourceUri": PAPER_SELECTION_WIDGET_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
        "openai/widgetAccessible": True,
    }


def _paper_selection_tool_meta(
    invoking: str = "Preparing paper selector...",
    invoked: str = "Paper selector ready.",
) -> Dict[str, Any]:
    return {
        "ui": {
            "resourceUri": PAPER_SELECTION_WIDGET_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


def _paper_selection_app_payload(
    *,
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
    message: str = "",
    download_selection_token: str = "",
    prompt_id: str = "",
    timeout_seconds: int = 0,
    expires_at: str = "",
    timeout_message: str = "",
    allow_reopen: bool = False,
    selection_timeout_seconds: int = 0,
    selection_expires_at: str = "",
    requested_count: int = 0,
    full_total: int = 0,
    persisted_selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    parse_ready_total = sum(
        1 for paper in papers if paper.get("parse_ready") is not False
    )
    semantics = _selection_semantics_name(selection_semantics)
    payload = {
        "status": "ok",
        "interaction": "mcp_app",
        "selection_token": selection_token,
        "papers": papers,
        "total": len(papers),
        "display_total": len(papers),
        "full_total": int(full_total or len(papers)),
        "requested_count": max(0, int(requested_count or 0)),
        "parse_ready_total": parse_ready_total,
        "save_path": save_path or DEFAULT_SAVE_PATH,
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "custom_save_path_confirmed": bool(custom_save_path_confirmed),
        "selection_semantics": semantics,
        "parse_execution": _workflow_parse_execution_name(parse_execution),
        "message": message or (
            "Select papers in the checkbox UI, then download selected papers."
            if semantics in {
                SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
            }
            else "Select papers in the checkbox UI, then parse selected papers."
        ),
        "_meta": _paper_selection_app_meta(),
    }
    if isinstance(persisted_selection, dict) and persisted_selection:
        payload["persisted_selection"] = persisted_selection
    if semantics in {SELECTION_SEMANTICS_DOWNLOAD_ONLY, SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE}:
        selection_timeout = int(selection_timeout_seconds or _compute_dynamic_parse_prompt_timeout(len(papers)))
        payload.update(
            {
                "selection_timeout_seconds": selection_timeout,
                "selection_expires_at": selection_expires_at
                or _parse_prompt_expires_at(selection_timeout),
                "selection_timeout_action": "no_download",
            }
        )
    if download_selection_token:
        timeout = int(timeout_seconds or _compute_dynamic_parse_prompt_timeout(len(papers)))
        payload.update(
            {
                "download_selection_token": download_selection_token,
                "prompt_id": prompt_id,
                "timeout_seconds": timeout,
                "expires_at": expires_at,
                "timeout_action": "no_parse",
                "terminal_on_timeout": True,
                "allow_reopen": bool(allow_reopen),
                "timeout_message": timeout_message
                or PARSE_PROMPT_TIMEOUT_MESSAGE.format(timeout_seconds=timeout),
            }
        )
    return payload


def _paper_selection_app_prompt(
    *,
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
    download_selection_token: str = "",
    prompt_id: str = "",
    timeout_seconds: int = 0,
    expires_at: str = "",
    timeout_message: str = "",
    allow_reopen: bool = False,
    selection_timeout_seconds: int = 0,
    selection_expires_at: str = "",
    requested_count: int = 0,
    full_total: int = 0,
) -> Dict[str, Any]:
    semantics = _selection_semantics_name(selection_semantics)
    prompt = {
        "interaction": "mcp_app",
        "instructions": (
            f"If the MCP host supports Apps, call {PAPER_SELECTION_WIDGET_TOOL} "
            "with this selection_token to render a checkbox UI."
        ),
        "render_tool": PAPER_SELECTION_WIDGET_TOOL,
        "resource_uri": PAPER_SELECTION_WIDGET_URI,
        "selection_token": selection_token,
        "save_path": save_path or DEFAULT_SAVE_PATH,
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "custom_save_path_confirmed": bool(custom_save_path_confirmed),
        "selection_semantics": semantics,
        "parse_execution": _workflow_parse_execution_name(parse_execution),
        "papers": papers,
        "total": len(papers),
        "display_total": len(papers),
        "full_total": int(full_total or len(papers)),
        "requested_count": max(0, int(requested_count or 0)),
        "parse_ready_total": sum(
            1 for paper in papers if paper.get("parse_ready") is not False
        ),
        "_meta": _paper_selection_app_meta(),
    }
    if semantics in {SELECTION_SEMANTICS_DOWNLOAD_ONLY, SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE}:
        selection_timeout = int(selection_timeout_seconds or _compute_dynamic_parse_prompt_timeout(len(papers)))
        prompt.update(
            {
                "selection_timeout_seconds": selection_timeout,
                "selection_expires_at": selection_expires_at
                or _parse_prompt_expires_at(selection_timeout),
                "selection_timeout_action": "no_download",
            }
        )
    if download_selection_token:
        timeout = int(timeout_seconds or _compute_dynamic_parse_prompt_timeout(len(papers)))
        prompt.update(
            {
                "download_selection_token": download_selection_token,
                "prompt_id": prompt_id,
                "timeout_seconds": timeout,
                "expires_at": expires_at,
                "timeout_action": "no_parse",
                "terminal_on_timeout": True,
                "allow_reopen": bool(allow_reopen),
                "timeout_message": timeout_message
                or PARSE_PROMPT_TIMEOUT_MESSAGE.format(timeout_seconds=timeout),
            }
        )
    return prompt


def _promote_paper_selection_app(result: Dict[str, Any]) -> Dict[str, Any]:
    """Expose the paper-selection widget metadata at the tool-result top level."""
    if not isinstance(result, dict):
        return result
    if str(result.get("status") or result.get("state") or "") in TERMINAL_PARSE_PROMPT_STATES:
        return result

    app = result.get("app")
    if not isinstance(app, dict):
        parse_prompt = result.get("parse_prompt")
        if isinstance(parse_prompt, dict) and isinstance(
            parse_prompt.get("app"), dict
        ):
            app = parse_prompt["app"]
            result["app"] = app

    if not isinstance(app, dict):
        return result

    surface = result.get("selection_surface")
    if isinstance(surface, dict) and surface.get("surface") != "mcp_app":
        return result

    app_meta = app.get("_meta")
    result["_meta"] = (
        app_meta if isinstance(app_meta, dict) else _paper_selection_app_meta()
    )
    result.setdefault("interaction", app.get("interaction", "mcp_app"))
    result.setdefault("selection_token", app.get("selection_token", ""))
    result.setdefault("total", app.get("total", 0))
    result.setdefault("parse_ready_total", app.get("parse_ready_total", 0))
    result.setdefault("save_path", app.get("save_path", DEFAULT_SAVE_PATH))
    result.setdefault("use_scihub", app.get("use_scihub", False))
    result.setdefault("mode", app.get("mode", "auto"))
    result.setdefault("backend", app.get("backend", ""))
    result.setdefault("force", app.get("force", False))
    result.setdefault(
        "custom_save_path_confirmed",
        app.get("custom_save_path_confirmed", False),
    )
    result.setdefault(
        "selection_semantics",
        app.get("selection_semantics", SELECTION_SEMANTICS_PARSE),
    )
    result.setdefault(
        "parse_execution", app.get("parse_execution", "background")
    )
    if "download_selection_token" in app:
        result.setdefault("download_selection_token", app.get("download_selection_token", ""))
    if "prompt_id" in app:
        result.setdefault("prompt_id", app.get("prompt_id", ""))
    if "timeout_seconds" in app:
        result.setdefault("timeout_seconds", app.get("timeout_seconds", 0))
    if "expires_at" in app:
        result.setdefault("expires_at", app.get("expires_at", ""))
    if "timeout_message" in app:
        result.setdefault("timeout_message", app.get("timeout_message", ""))
    result.setdefault("recommended_tool", PAPER_SELECTION_WIDGET_TOOL)
    result.setdefault("recommended_selected_indices", "")
    result.setdefault("parse_decision_required", True)
    result.setdefault("requires_user_parse_decision", True)
    return result


def _should_promote_paper_selection_app(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("status") or result.get("state") or "") in TERMINAL_PARSE_PROMPT_STATES:
        return False
    if str(result.get("prompt_state") or "") in TERMINAL_PARSE_PROMPT_STATES:
        return False
    if bool(result.get("parse_decision_required")):
        return True
    if result.get("recommended_tool") == PAPER_SELECTION_WIDGET_TOOL:
        return True

    parse_prompt = result.get("parse_prompt")
    if isinstance(parse_prompt, dict):
        return _should_promote_paper_selection_app(parse_prompt)
    return False


_WIDGET_META_KEYS = {"_meta", "app"}


def _strip_widget_meta(obj: Any) -> Any:
    """Remove ``_meta`` and ``app`` keys from a dict so nested objects
    embedded inside a tool result don't trigger duplicate MCP App widgets.

    Returns a shallow copy of the dict (or the original value for non-dicts).
    """
    if not isinstance(obj, dict):
        return obj
    return {k: v for k, v in obj.items() if k not in _WIDGET_META_KEYS}


# ===========================================================================
# Complex async prompt functions
# ===========================================================================
# These call MCP tools (parse_selected_papers, submit_parse_job) and
# UI helpers (_attach_local_selection_ui).  Optional callable overrides avoid
# circular imports from the tools package.  Lazy imports serve as fallbacks.


async def _prompt_parse_saved_pdfs(
    *,
    papers: List[Dict[str, Any]],
    query: str,
    sources: str,
    save_path: str,
    ctx: Optional[Any] = None,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    parse_execution: str = "background",
    custom_save_path_confirmed: bool = False,
    _parse_selected_papers_fn: Optional[Any] = None,
    _submit_parse_job_fn: Optional[Any] = None,
    _attach_local_selection_ui_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Prompt the user to parse one or more saved PDFs.

    Parameters
    ----------
    _parse_selected_papers_fn : async callable | None
        Override for ``parse_selected_papers``.
    _submit_parse_job_fn : async callable | None
        Override for ``submit_parse_job``.
    _attach_local_selection_ui_fn : async callable | None
        Override for ``_attach_local_selection_ui``.
    """
    # Callers MUST provide these callables (engine layer has no MCP dependency)
    if _parse_selected_papers_fn is None:
        raise RuntimeError(
            "_parse_selected_papers_fn must be provided by the caller "
            "(this engine function has no MCP dependency)"
        )
    if _submit_parse_job_fn is None:
        raise RuntimeError(
            "_submit_parse_job_fn must be provided by the caller "
            "(this engine function has no MCP dependency)"
        )

    session = await asyncio.to_thread(
        cache_create_search_session,
        query,
        sources,
        papers,
        {
            "interaction": "download_saved_pdf_parse_prompt",
            "trigger": "pdf_saved",
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

    fallback: Dict[str, Any] = {
        "status": "elicitation_unavailable",
        "interaction": "backend_session_numbered_selection",
        "selection_token": session["selection_token"],
        "instructions": (
            "PDF saved. Present the numbered papers to the user. To parse "
            "selected PDFs, call parse_selected_papers(selection_token=<token>, "
            "selected_indices='1') or selected_indices='all'."
        ),
        "papers": candidates,
        "total": len(candidates),
        "parse_ready_total": len(selectable),
        "parse_execution": parse_execution_name,
    }
    fallback["app"] = _paper_selection_app_prompt(
        selection_token=session["selection_token"],
        papers=candidates,
        save_path=save_path,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )

    if not selectable:
        return {**fallback, "status": "no_parse_ready_pdfs"}

    if parse_execution_name == "none":
        return {
            **fallback,
            "status": "ok",
            "message": (
                "PDF saved. MinerU parsing was not started because "
                "parse_execution is set to 'none'."
            ),
        }

    if parse_execution_name == "prompt":
        prompt_response = {
            **fallback,
            "status": "ok",
            "parse_decision_required": True,
            "requires_user_parse_decision": True,
            "recommended_tool": PAPER_SELECTION_WIDGET_TOOL,
            "recommended_selected_indices": "",
            "message": (
                "PDF saved. Select PDFs in the checkbox UI or use numbered "
                "indices before MinerU parsing."
            ),
        }
        if (
            len(candidates) > AUTO_PARSE_SAVED_PDF_LIMIT
            and _attach_local_selection_ui_fn is not None
        ):
            await _attach_local_selection_ui_fn(
                prompt_response,
                selection_token=session["selection_token"],
                papers=candidates,
                save_path=save_path,
                use_scihub=False,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
                force_open=True,
            )
        return _promote_paper_selection_app(prompt_response)

    if len(candidates) <= AUTO_PARSE_SAVED_PDF_LIMIT:
        selected_indices = [
            int(candidate["index"]) for candidate in selectable
        ]
        selected_indices_arg = ",".join(
            str(index) for index in selected_indices
        )
        if parse_execution_name == "sync":
            parse_result = await _parse_selected_papers_fn(
                selection_token=session["selection_token"],
                selected_indices=selected_indices_arg,
                save_path=save_path,
                use_scihub=False,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
            )
            return {
                **parse_result,
                "interaction": "auto_parse_saved_pdfs",
                "selection_token": session["selection_token"],
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
            selection_token=session["selection_token"],
            selected_indices=selected_indices_arg,
            save_path=save_path,
            use_scihub=False,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        return {
            "status": (
                parse_job.get("status", "submitted")
                if isinstance(parse_job, dict)
                else "submitted"
            ),
            "interaction": "auto_parse_saved_pdfs",
            "selection_token": session["selection_token"],
            "selected_indices": selected_indices,
            "recommended_tool": "get_parse_job_status",
            "recommended_selected_indices": selected_indices_arg,
            "auto_parse_limit": AUTO_PARSE_SAVED_PDF_LIMIT,
            "parse_execution": parse_execution_name,
            "parse_job": parse_job,
            "message": (
                f"Saved {len(candidates)} PDF(s), which is at or below "
                f"the auto-parse limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. "
                "Submitted a MinerU parse job."
            ),
        }

    if ctx is None:
        if _attach_local_selection_ui_fn is not None:
            await _attach_local_selection_ui_fn(
                fallback,
                selection_token=session["selection_token"],
                papers=candidates,
                save_path=save_path,
                use_scihub=False,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
                force_open=True,
            )
        fallback["parse_decision_required"] = True
        fallback["requires_user_parse_decision"] = True
        fallback["recommended_tool"] = PAPER_SELECTION_WIDGET_TOOL
        fallback["recommended_selected_indices"] = ""
        return _promote_paper_selection_app(fallback)

    options = [_elicitation_option_label(candidate) for candidate in selectable]
    schema = _build_paper_selection_schema(options)
    try:
        elicitation = await ctx.elicit(
            message="PDF saved. Select PDFs for MinerU PDF parsing.",
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
                "Use parse_selected_papers with numbered indices if needed."
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
                "No PDFs were selected. Use parse_selected_papers "
                "with numbered indices if needed."
            ),
        }

    selected_indices_arg = ",".join(
        str(index) for index in selected_indices
    )
    if parse_execution_name == "sync":
        parse_result = await _parse_selected_papers_fn(
            selection_token=session["selection_token"],
            selected_indices=selected_indices_arg,
            save_path=save_path,
            use_scihub=False,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
        return {
            **parse_result,
            "interaction": "elicitation",
            "selection_token": session["selection_token"],
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
        selection_token=session["selection_token"],
        selected_indices=selected_indices_arg,
        save_path=save_path,
        use_scihub=False,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
    )
    return {
        "status": (
            parse_job.get("status", "submitted")
            if isinstance(parse_job, dict)
            else "submitted"
        ),
        "interaction": "elicitation",
        "selection_token": session["selection_token"],
        "selected_indices": selected_indices,
        "recommended_tool": "get_parse_job_status",
        "recommended_selected_indices": selected_indices_arg,
        "parse_execution": parse_execution_name,
        "parse_job": parse_job,
        "message": "Submitted a MinerU parse job for the selected PDFs.",
    }


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
    ctx: Optional[Any] = None,
    custom_save_path_confirmed: bool = False,
    _parse_selected_papers_fn: Optional[Any] = None,
    _submit_parse_job_fn: Optional[Any] = None,
    _attach_local_selection_ui_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build a parse prompt after a batch download completes."""
    # Callers MUST provide these callables (engine layer has no MCP dependency)
    if _parse_selected_papers_fn is None:
        raise RuntimeError(
            "_parse_selected_papers_fn must be provided by the caller "
            "(this engine function has no MCP dependency)"
        )
    if _submit_parse_job_fn is None:
        raise RuntimeError(
            "_submit_parse_job_fn must be provided by the caller "
            "(this engine function has no MCP dependency)"
        )
    terminal_prompt = _terminal_parse_prompt_for_download(selection_token)
    if terminal_prompt and not _parse_prompt_allow_reopen():
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
        cache_create_search_session,
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

    if (
        len(candidates) <= AUTO_PARSE_SAVED_PDF_LIMIT
        and _saved_pdf_batch_prompt_enabled()
    ):
        recent_papers = _recent_saved_pdf_papers(
            save_path,
            window_seconds=_saved_pdf_batch_window_seconds(),
        )
        if len(recent_papers) > AUTO_PARSE_SAVED_PDF_LIMIT:
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
                _attach_local_selection_ui_fn=_attach_local_selection_ui_fn,
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
        if (
            len(candidates) > AUTO_PARSE_SAVED_PDF_LIMIT
            and _attach_local_selection_ui_fn is not None
        ):
            await _attach_local_selection_ui_fn(
                fallback,
                selection_token=parse_session["selection_token"],
                papers=candidates,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
                force_open=True,
            )
        await asyncio.to_thread(_write_pending_parse_prompt_state, fallback)
        return _promote_paper_selection_app(fallback)

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
        if _attach_local_selection_ui_fn is not None:
            await _attach_local_selection_ui_fn(
                fallback,
                selection_token=parse_session["selection_token"],
                papers=candidates,
                save_path=save_path,
                use_scihub=use_scihub,
                mode=mode,
                backend=backend,
                force=force,
                custom_save_path_confirmed=custom_save_path_confirmed,
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
        return _promote_paper_selection_app(
            {
                **prompted,
            }
        )

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

    selected_indices_arg = ",".join(
        str(index) for index in selected_indices
    )
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
    _attach_local_selection_ui_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Pre-download selection prompt for large batches."""
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

    parse_session = await asyncio.to_thread(
        cache_create_search_session,
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
        else "Select papers before download; only selected papers will be "
        "saved and parsed."
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
        "instructions": (
            f"More than 10 papers were requested. {action_message}"
        ),
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
    if _attach_local_selection_ui_fn is not None:
        await _attach_local_selection_ui_fn(
            prompt,
            selection_token=parse_session["selection_token"],
            papers=candidates,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
            custom_save_path_confirmed=custom_save_path_confirmed,
            force_open=True,
            selection_semantics=semantics,
            parse_execution=parse_execution,
        )
    return _promote_paper_selection_app(prompt)


# ===========================================================================
# _create_paper_selection_result
# ===========================================================================

async def _arxiv_metadata_for_id(arxiv_id: str) -> Dict[str, Any]:
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
    _rank_papers_for_profile_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create a paper selection session from search results."""
    if _rank_papers_for_profile_fn is None:
        from .paper import _rank_papers_for_profile  # noqa: PLC0415
        _rank_papers_for_profile_fn = _rank_papers_for_profile

    semantics = _selection_semantics_name(selection_semantics)
    papers = search_result.get("papers", [])
    if not isinstance(papers, list):
        papers = []

    ranked_papers = _rank_papers_for_profile_fn(
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
        cache_create_search_session,
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
            cache_create_search_session,
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
    }
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
    return _promote_paper_selection_app(result)


# ---------------------------------------------------------------------------
# Re-imports from engine.download (canonical source for these functions)
# Placed at bottom to avoid circular imports.
# ---------------------------------------------------------------------------
from .download import (  # noqa: E402,F401
    _after_saved_pdf,
    _after_saved_pdfs,
    _candidate_download_id,
    _changed_pdf_paths,
    _download_manifest_path,
    _download_source_pdf,
    _downloaded_pdf_paper,
    _downloaded_pdf_papers,
    _existing_pdf_candidates,
    _find_existing_pdf,
    _is_valid_pdf_file,
    _paper_from_download_metadata,
    _pdf_path_from_result,
    _pdf_paths_from_result,
    _pdf_result_metadata,
    _read_source_paper,
    _recent_saved_pdf_papers,
    _saved_pdf_batch_prompt_enabled,
    _saved_pdf_batch_window_seconds,
    _snapshot_pdf_files,
)
