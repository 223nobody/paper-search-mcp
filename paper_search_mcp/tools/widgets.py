# paper_search_mcp/tools/widgets.py
"""MCP resource widgets and render tools for the paper-search MCP server.

Extracted from server.py.  Provides ``register_widget_tools(mcp)`` which
registers the two MCP resource widgets (paper selection, MinerU key setup)
and their companion render tools on a FastMCP instance.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from ..cache import (
    get_search_session as cache_get_search_session,
    read_selection_ui_state as cache_read_selection_ui_state,
    write_selection_ui_state as cache_write_selection_ui_state,
)
from ..engine.paper import _paper_parse_candidate
from ..engine.parse import (
    AUTO_PARSE_SAVED_PDF_LIMIT,
    MINERU_KEY_WIDGET_TOOL,
    MINERU_KEY_WIDGET_URI,
    PAPER_SELECTION_WIDGET_TOOL,
    PAPER_SELECTION_WIDGET_URI,
    SELECTION_SEMANTICS_DOWNLOAD_ONLY,
    SELECTION_SEMANTICS_PARSE,
    _mineru_api_key_configured,
    _mineru_key_setup_prompt,
    _codex_app_display_candidates,
    _paper_selection_app_payload,
    _reindexed_display_candidates,
    _selection_semantics_name,
    _selection_surface_policy,
    _workflow_parse_execution_name,
)
from ..ui.html_templates import (
    MINERU_KEY_WIDGET_HTML,
    PAPER_SELECTION_WIDGET_HTML,
)
from ..ui.server import (
    LOCAL_PAPER_SELECTION_TOOL,
    _create_local_selection_page,
    open_paper_selection_page as _open_local_paper_selection_page,
)
from ..utils import DEFAULT_SAVE_PATH, detect_host, host_supports_mcp_apps_widget
from ..selection_confirmation import (
    confirmation_required_response,
    consume_selection_confirmation_token,
    create_selection_confirmation_token,
    format_selected_indices as _format_indices,
    normalize_selected_indices,
    selection_revision as _selection_revision,
)
from ..widgets.response import widget_tool_result


# ---------------------------------------------------------------------------
# Resource: paper-selection widget
# ---------------------------------------------------------------------------


async def _handle_paper_selection_widget() -> str:
    """Return the checkbox UI used by MCP Apps-capable hosts."""
    return PAPER_SELECTION_WIDGET_HTML


_PAPER_SELECTION_RESOURCE_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
        },
    },
    "openai/widgetDescription": (
        "Checkbox selector for choosing papers to download or parse with MinerU."
    ),
    "openai/widgetPrefersBorder": True,
    "openai/widgetCSP": {
        "connect_domains": [],
        "resource_domains": [],
    },
}


# ---------------------------------------------------------------------------
# Tool: render_paper_selection_app
# ---------------------------------------------------------------------------


async def _handle_render_paper_selection_app(
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
    """Render a checkbox paper selector for MCP Apps-capable hosts.

    If papers are omitted, the tool loads candidates from the saved selection
    session identified by selection_token.
    """
    candidates = papers if isinstance(papers, list) else []
    session: Dict[str, Any] = {}
    if not candidates:
        session = await asyncio.to_thread(
            cache_get_search_session, selection_token
        )
        stored_papers = session.get("papers", []) if session else []
        if not isinstance(stored_papers, list):
            stored_papers = []
        candidates = [
            _paper_parse_candidate(paper, index + 1)
            for index, paper in enumerate(stored_papers)
        ]
    metadata = session.get("metadata", {}) if isinstance(session, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    effective_semantics = selection_semantics or str(
        metadata.get("selection_semantics") or SELECTION_SEMANTICS_PARSE
    )
    effective_parse_execution = parse_execution or str(
        metadata.get("parse_execution") or "background"
    )
    requested_count = int(metadata.get("requested_count") or 0)
    full_total = int(metadata.get("full_total") or len(candidates))
    app_candidates = _codex_app_display_candidates(
        candidates,
        requested_count=requested_count,
    )
    app_candidates = _reindexed_display_candidates(app_candidates)
    ui_state = await asyncio.to_thread(
        cache_read_selection_ui_state, selection_token
    ) if session else {}
    persisted_selection = _selection_state_payload(
        selection_token, session, ui_state
    ) if session else {}
    payload = _paper_selection_app_payload(
        selection_token=selection_token,
        papers=app_candidates,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        selection_semantics=effective_semantics,
        parse_execution=effective_parse_execution,
        requested_count=requested_count,
        full_total=full_total,
        persisted_selection=persisted_selection,
    )
    payload["detected_host"] = detect_host()
    payload["app_widget_supported"] = host_supports_mcp_apps_widget()
    payload["selection_surface"] = _selection_surface_policy(force_open=True)
    if not payload["app_widget_supported"]:
        payload["fallback_reason"] = "host_without_mcp_app_sandbox"
        payload["fallback_tool"] = LOCAL_PAPER_SELECTION_TOOL
        payload["fallback_instructions"] = (
            f"Call {LOCAL_PAPER_SELECTION_TOOL} with this selection_token to "
            "open the localhost checkbox selector."
        )
    return widget_tool_result(payload, _RENDER_PAPER_SELECTION_TOOL_META)


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
            confirmed_indices = normalize_selected_indices(confirmed_arg, total)
        except ValueError:
            confirmed_indices = []
    effective_indices = confirmed_indices or normalized
    revision = _selection_revision(session)
    return {
        "selection_token": selection_token,
        "selected_indices": effective_indices,
        "selected_indices_arg": _format_indices(effective_indices),
        "draft_selected_indices": normalized,
        "draft_selected_indices_arg": _format_indices(normalized),
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


async def _handle_get_paper_selection_state(selection_token: str) -> Dict[str, Any]:
    """Return persisted checkbox state for a sandbox that re-rendered."""
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    if not session:
        return {
            "status": "not_found",
            "selection_token": selection_token,
            "selected_indices": [],
            "selected_indices_arg": "",
            "message": "Search session not found.",
        }
    state = await asyncio.to_thread(cache_read_selection_ui_state, selection_token)
    return {
        "status": "ok",
        **_selection_state_payload(selection_token, session, state),
    }


async def _handle_save_paper_selection_state(
    selection_token: str,
    selected_indices: str = "",
    client_instance_id: str = "",
    selection_revision: str = "",
) -> Dict[str, Any]:
    """Persist in-progress checkbox state so MCP App re-renders recover it."""
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
    if not str(selected_indices or "").strip():
        indices: List[int] = []
    else:
        try:
            indices = normalize_selected_indices(selected_indices, len(papers))
        except ValueError as exc:
            return {
                "status": "invalid_selection",
                "selection_token": selection_token,
                "message": str(exc),
                "total": len(papers),
            }
    revision = _selection_revision(session)
    if selection_revision and str(selection_revision) != revision:
        return {
            "status": "stale_selection",
            "selection_token": selection_token,
            "message": "Selection session changed; refresh the selector before saving.",
            "selection_revision": revision,
            "client_selection_revision": selection_revision,
        }
    stored = await asyncio.to_thread(
        cache_write_selection_ui_state,
        selection_token,
        {
            "selected_indices": indices,
            "selected_indices_arg": _format_indices(indices),
            "selection_revision": revision,
            "client_instance_id": client_instance_id or "",
            "submitted": False,
        },
    )
    return {
        "status": "ok",
        "selection_token": selection_token,
        "selected_indices": indices,
        "selected_indices_arg": _format_indices(indices),
        "selection_revision": revision,
        "updated_at": stored.get("updated_at", ""),
    }


async def _confirm_selection_metadata(
    selection_token: str,
    selected_indices: str,
    confirmed_via: str,
    confirmation_token: str = "",
    action: str = "download",
    save_path: str = "",
) -> Dict[str, Any]:
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
    if len(papers) > AUTO_PARSE_SAVED_PDF_LIMIT:
        return await asyncio.to_thread(
            consume_selection_confirmation_token,
            selection_token=selection_token,
            selected_indices=selected_indices,
            confirmation_token=confirmation_token,
            confirmed_via=confirmed_via,
            action=action or "download",
            save_path=save_path or "",
        )
    try:
        indices = normalize_selected_indices(selected_indices or "", len(papers))
    except ValueError as exc:
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "message": str(exc),
            "total": len(papers),
        }
    revision = _selection_revision(session)
    confirmed_arg = _format_indices(indices)
    await asyncio.to_thread(
        cache_write_selection_ui_state,
        selection_token,
        {
            "selected_indices": indices,
            "selected_indices_arg": confirmed_arg,
            "selection_revision": revision,
            "submitted": True,
        },
    )
    return {
        "status": "confirmed",
        "selection_token": selection_token,
        "selected_indices": indices,
        "selected_indices_arg": confirmed_arg,
        "selection_revision": revision,
        "confirmed_via": confirmed_via,
        "total": len(indices),
    }


async def _handle_confirm_paper_selection(
    selection_token: str,
    selected_indices: str = "",
    confirmed_via: str = "mcp_app",
    confirmation_token: str = "",
) -> Dict[str, Any]:
    """Record the user-confirmed checkbox selection without downloading.

    This tool is for MCP App widget callbacks only.  LLM models calling it
    without a valid confirmation_token from a real checkbox UI will be
    rejected when the paper count exceeds AUTO_PARSE_SAVED_PDF_LIMIT.
    """
    # ── Hardened pre-gate: when >10 papers, a valid confirmation_token
    #     from the checkbox UI is mandatory — no programmatic bypass. ──
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    if session:
        papers = session.get("papers", [])
        if not isinstance(papers, list):
            papers = []
        if len(papers) > AUTO_PARSE_SAVED_PDF_LIMIT and not confirmation_token:
            from ..selection_confirmation import confirmation_required_response as _crr

            return _crr(
                selection_token=selection_token,
                selected_indices=selected_indices,
            )
    return await _confirm_selection_metadata(
        selection_token,
        selected_indices,
        confirmed_via or "mcp_app",
        confirmation_token=confirmation_token,
    )


async def _handle_download_confirmed_paper_selection(
    selection_token: str,
    selected_indices: str = "",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    custom_save_path_confirmed: bool = False,
    parse_execution: str = "none",
    confirmation_token: str = "",
) -> Dict[str, Any]:
    """Download only after the MCP App has submitted a user selection."""
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    papers = session.get("papers", []) if isinstance(session, dict) else []
    if not isinstance(papers, list):
        papers = []
    if len(papers) > AUTO_PARSE_SAVED_PDF_LIMIT and not confirmation_token:
        created = await asyncio.to_thread(
            create_selection_confirmation_token,
            selection_token=selection_token,
            selected_indices=selected_indices,
            action="download",
            save_path=save_path,
        )
        if created.get("status") != "ok":
            return created
        confirmation_token = str(created.get("selection_confirmation_token") or "")
    confirmation = await _confirm_selection_metadata(
        selection_token,
        selected_indices,
        "mcp_app",
        confirmation_token=confirmation_token,
        save_path=save_path,
    )
    if confirmation.get("status") != "confirmed":
        return confirmation

    from .orchestration import _run_download_selected_papers

    return await _run_download_selected_papers(
        selection_token=selection_token,
        selected_indices=confirmation["selected_indices_arg"],
        save_path=save_path,
        use_scihub=use_scihub,
        concurrency=0,
        parse_execution=_workflow_parse_execution_name(parse_execution or "none"),
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        large_batch_selection="never",
        bypass_large_batch_selection=True,
        _caller="confirmed_download_handler",
    )


async def _handle_open_paper_url_in_browser(
    selection_token: str,
    paper_index: int,
    url_kind: str = "paper",
) -> Dict[str, Any]:
    """Open a paper or PDF URL from the stored session via the host browser."""
    kind = (url_kind or "paper").strip().lower()
    if kind not in {"paper", "pdf"}:
        return {
            "status": "invalid_url_kind",
            "selection_token": selection_token,
            "paper_index": paper_index,
            "message": "url_kind must be 'paper' or 'pdf'.",
        }
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    papers = session.get("papers", []) if isinstance(session, dict) else []
    if not isinstance(papers, list):
        papers = []
    if paper_index < 1 or paper_index > len(papers) or not isinstance(papers[paper_index - 1], dict):
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "paper_index": paper_index,
            "message": "Paper index is not available in this session.",
        }
    paper = papers[paper_index - 1]
    url = ""
    if kind == "pdf":
        url = str(paper.get("pdf_url") or "").strip()
    if not url:
        url = str(
            paper.get("original_url") or paper.get("url") or paper.get("pdf_url") or ""
        ).strip()
    if not url:
        return {
            "status": "no_url",
            "selection_token": selection_token,
            "paper_index": paper_index,
            "url_kind": kind,
            "message": "No URL is available for this paper.",
        }
    from ..utils import open_url_in_host

    opened = bool(await asyncio.to_thread(open_url_in_host, url))
    return {
        "status": "ok" if opened else "open_failed",
        "selection_token": selection_token,
        "paper_index": paper_index,
        "url_kind": kind,
        "url": url,
        "opened": opened,
        "message": "Opened in browser." if opened else "Could not open automatically; copy the URL.",
    }


_RENDER_PAPER_SELECTION_TOOL_META = {
    "ui": {
        "resourceUri": PAPER_SELECTION_WIDGET_URI,
        "visibility": ["model", "app"],
    },
    "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
    "openai/widgetAccessible": True,
    "openai/toolInvocation/invoking": "Opening paper selector...",
    "openai/toolInvocation/invoked": "Paper selector ready.",
}

# Meta for tools that ONLY the MCP App widget (or local browser UI) should call.
# The model/LLM must NOT see or invoke these — they are internal confirmation hooks.
# "visibility": ["app"] hides them from the model in spec-compliant hosts;
# widgetAccessible is explicitly False to prevent host-level over-exposure.
_APP_ONLY_TOOL_META = {
    "ui": {"visibility": ["app"]},
    "openai/widgetAccessible": False,
}


# ---------------------------------------------------------------------------
# Tool: open_paper_selection_page  (local-browser fallback)
# ---------------------------------------------------------------------------


async def _handle_open_paper_selection_page(
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
    """Open a local browser checkbox selector for clients without MCP Apps UI.

    This fallback renders a normal localhost HTML page, so it works even when
    the chat host cannot display MCP Apps widgets in the conversation.
    """
    candidates = papers if isinstance(papers, list) else []
    session: Dict[str, Any] = {}
    if not candidates:
        session = await asyncio.to_thread(
            cache_get_search_session, selection_token
        )
        stored_papers = session.get("papers", []) if session else []
        if not isinstance(stored_papers, list):
            stored_papers = []
        candidates = [
            _paper_parse_candidate(paper, index + 1)
            for index, paper in enumerate(stored_papers)
        ]
    metadata = session.get("metadata", {}) if isinstance(session, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    effective_semantics = selection_semantics or str(
        metadata.get("selection_semantics") or SELECTION_SEMANTICS_PARSE
    )
    effective_parse_execution = parse_execution or str(
        metadata.get("parse_execution") or "background"
    )
    requested_count = int(metadata.get("requested_count") or 0)
    full_total = int(metadata.get("full_total") or len(candidates))
    display_candidates = _codex_app_display_candidates(
        candidates,
        requested_count=requested_count,
    )
    display_candidates = _reindexed_display_candidates(display_candidates)

    page = _create_local_selection_page(
        selection_token=selection_token,
        papers=display_candidates,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
        custom_save_path_confirmed=custom_save_path_confirmed,
        selection_semantics=effective_semantics,
        parse_execution=effective_parse_execution,
    )
    opened = False
    if open_browser:
        from ..utils import open_url_in_host
        opened = await asyncio.to_thread(open_url_in_host, page["url"])
    try:
        from ..ui import server as _ui_server

        local_server = getattr(_ui_server, "_LOCAL_SELECTION_SERVER", None)
        host, port = local_server.server_address[:2] if local_server else ("", 0)
    except Exception:
        host, port = "", 0

    return {
        "status": "ok",
        "interaction": "local_browser_checkbox",
        "selection_token": selection_token,
        "url": page["url"],
        "page_id": page["page_id"],
        "opened": opened,
        "selection_timeout_seconds": int(page.get("selection_timeout_seconds") or 0),
        "selection_expires_at": str(page.get("selection_expires_at") or ""),
        "server_pid": os.getpid(),
        "local_host": str(host),
        "local_port": int(port or 0),
        "papers": display_candidates,
        "total": len(display_candidates),
        "display_total": len(display_candidates),
        "full_total": full_total,
        "requested_count": requested_count,
        "parse_ready_total": sum(
            1
            for paper in display_candidates
            if paper.get("parse_ready") is not False
        ),
        "selection_semantics": _selection_semantics_name(effective_semantics),
        "parse_execution": _workflow_parse_execution_name(
            effective_parse_execution
        ),
        "message": (
            "Open the URL to select papers with checkboxes and download them "
            "from the browser page."
            if _selection_semantics_name(effective_semantics)
            == SELECTION_SEMANTICS_DOWNLOAD_ONLY
            else "Open the URL to select papers with checkboxes and parse "
            "them from the browser page."
        ),
    }


# ---------------------------------------------------------------------------
# Resource: mineru-api-key widget
# ---------------------------------------------------------------------------


async def _handle_mineru_api_key_setup_widget() -> str:
    """Return the MinerU API key setup UI for MCP Apps-capable hosts."""
    return MINERU_KEY_WIDGET_HTML


_MINERU_KEY_RESOURCE_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
        },
    },
    "openai/widgetDescription": (
        "Form for saving PAPER_SEARCH_MCP_MINERU_API_KEY to the project "
        ".env file."
    ),
    "openai/widgetPrefersBorder": True,
    "openai/widgetCSP": {
        "connect_domains": [],
        "resource_domains": [],
    },
}


# ---------------------------------------------------------------------------
# Tool: render_mineru_api_key_setup_app
# ---------------------------------------------------------------------------


async def _handle_render_mineru_api_key_setup_app(
    reason: str = "missing",
    message: str = "",
) -> Dict[str, Any]:
    """Render a MinerU API key setup form for MCP Apps-capable hosts."""
    prompt = _mineru_key_setup_prompt(reason=reason, message=message)
    return {
        **prompt,
        "configured": _mineru_api_key_configured(),
    }


_RENDER_MINERU_KEY_TOOL_META = {
    "ui": {
        "resourceUri": MINERU_KEY_WIDGET_URI,
        "visibility": ["model", "app"],
    },
    "openai/outputTemplate": MINERU_KEY_WIDGET_URI,
    "openai/widgetAccessible": True,
    "openai/toolInvocation/invoking": "Opening MinerU setup...",
    "openai/toolInvocation/invoked": "MinerU setup ready.",
}


# ---------------------------------------------------------------------------
# Tool: select_papers_tui  (terminal fallback for CLI hosts)
# ---------------------------------------------------------------------------


async def _handle_select_papers_tui(
    selection_token: str,
    download_only: bool = False,
    papers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Render an interactive terminal paper selector using rich.

    This is the CLI fallback for hosts that cannot render MCP Apps widgets
    and where the user prefers the terminal over a browser.
    """
    candidates = papers if isinstance(papers, list) else []
    session: Dict[str, Any] = {}
    if not candidates:
        session = await asyncio.to_thread(
            cache_get_search_session, selection_token
        )
        stored_papers = session.get("papers", []) if session else []
        if not isinstance(stored_papers, list):
            stored_papers = []
        candidates = [
            _paper_parse_candidate(paper, index + 1)
            for index, paper in enumerate(stored_papers)
        ]

    from ..ui.tui import render_paper_selection_tui as _tui

    response = await asyncio.to_thread(
        _tui,
        papers=candidates,
        selection_token=selection_token,
        download_only=download_only,
    )

    if not response or not response.strip():
        return {
            "status": "cancelled",
            "selection_token": selection_token,
            "selected_indices": "",
            "message": "User cancelled the terminal selection.",
        }

    return {
        "status": "selected",
        "selection_token": selection_token,
        "selected_indices": response,
        "message": (
            f"User selected papers: {response}. "
            f"Proceed with download_selected_papers(selection_token="
            f"'{selection_token}', selected_indices='{response}')."
        ),
        "next_tool": "download_selected_papers",
    }


# ===========================================================================
# Public registration entry-point
# ===========================================================================


def register_widget_tools(mcp) -> None:  # type: ignore[no-untyped-def]
    """Register MCP resource widgets and their render tools.

    Call once during startup with the FastMCP server instance::

        from paper_search_mcp.tools.widgets import register_widget_tools
        register_widget_tools(mcp)
    """

    # ---- paper-selection widget (MCP resource) -----------------------------
    mcp.resource(
        PAPER_SELECTION_WIDGET_URI,
        name="Paper Selection Widget",
        mime_type="text/html;profile=mcp-app",
        meta=_PAPER_SELECTION_RESOURCE_META,
    )(_handle_paper_selection_widget)

    # ---- render_paper_selection_app (MCP tool) -----------------------------
    mcp.tool(
        name=PAPER_SELECTION_WIDGET_TOOL,
        meta=_RENDER_PAPER_SELECTION_TOOL_META,
    )(_handle_render_paper_selection_app)

    # ---- open_paper_selection_page (MCP tool, local browser fallback) ------
    mcp.tool(
        name=LOCAL_PAPER_SELECTION_TOOL,
        structured_output=True,
    )(_handle_open_paper_selection_page)

    # ---- MCP App state/action helpers --------------------------------------
    mcp.tool(
        name="get_paper_selection_state",
        meta=_APP_ONLY_TOOL_META,
        structured_output=True,
    )(
        _handle_get_paper_selection_state
    )
    mcp.tool(
        name="save_paper_selection_state",
        meta=_APP_ONLY_TOOL_META,
        structured_output=True,
    )(
        _handle_save_paper_selection_state
    )
    mcp.tool(
        name="confirm_paper_selection",
        meta=_APP_ONLY_TOOL_META,
        structured_output=True,
    )(
        _handle_confirm_paper_selection
    )
    mcp.tool(
        name="download_confirmed_paper_selection",
        meta=_APP_ONLY_TOOL_META,
        structured_output=True,
    )(
        _handle_download_confirmed_paper_selection
    )
    mcp.tool(
        name="open_paper_url_in_browser",
        meta=_APP_ONLY_TOOL_META,
        structured_output=True,
    )(
        _handle_open_paper_url_in_browser
    )

    # ---- mineru-api-key widget (MCP resource) ------------------------------
    mcp.resource(
        MINERU_KEY_WIDGET_URI,
        name="MinerU API Key Setup Widget",
        mime_type="text/html;profile=mcp-app",
        meta=_MINERU_KEY_RESOURCE_META,
    )(_handle_mineru_api_key_setup_widget)

    # ---- render_mineru_api_key_setup_app (MCP tool) ------------------------
    mcp.tool(
        name=MINERU_KEY_WIDGET_TOOL,
        meta=_RENDER_MINERU_KEY_TOOL_META,
        structured_output=True,
    )(_handle_render_mineru_api_key_setup_app)

    # ---- select_papers_tui (terminal fallback for CLI hosts) ----------------
    mcp.tool(
        name="select_papers_tui",
    )(_handle_select_papers_tui)
