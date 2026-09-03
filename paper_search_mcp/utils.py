import re
import time
from pathlib import Path
from typing import Any, Dict


DEFAULT_SAVE_PATH = "~/Desktop/papers"


def resolve_save_path(save_path: str = DEFAULT_SAVE_PATH) -> str:
    """Expand a user-facing save path such as ~/Desktop/papers to an absolute path."""
    value = (save_path or DEFAULT_SAVE_PATH).strip() or DEFAULT_SAVE_PATH
    return str(Path(value).expanduser().resolve())


def extract_doi(text: str) -> str:
    """Extract DOI from arbitrary text or URL if present."""
    if not text:
        return ""
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.IGNORECASE)
    return match.group(0).rstrip(".,;)") if match else ""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text content from a PDF file using pypdf.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted text as a single string (pages joined by newlines).
        Returns empty string if no text could be extracted.
    """
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    text_parts = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text)
    return "\n".join(text_parts)


def is_pdf_content_type(content_type: str) -> bool:
    """Check whether a Content-Type header indicates a PDF.

    Args:
        content_type: Value of the Content-Type response header.

    Returns:
        True if the content type appears to be PDF.
    """
    return "pdf" in (content_type or "").lower()


# ===========================================================================
# Host environment detection — determines which MCP client is running the
# server so the selection UI can adapt (MCP Apps widget vs. local browser).
# ===========================================================================

import os as _os
from functools import lru_cache as _lru_cache


@_lru_cache(maxsize=1)
def detect_host() -> str:
    """Detect which MCP host is running the server.

    Returns one of:
      - ``"codex"``                : OpenAI Codex Desktop or CLI
      - ``"codex_vscode"``         : OpenAI Codex IDE extension in VS Code
      - ``"claude_code_vscode"``   : Claude Code VS Code extension
      - ``"claude_code_desktop"``  : Claude Code Desktop (standalone GUI app)
      - ``"claude_code_cli"``      : Claude Code CLI (terminal)
      - ``"claude_desktop"``       : Claude Desktop (legacy standalone app)
      - ``"dsh"``                  : DeepSeek Harness / dsh
      - ``"zcode"``                : ZCode host
      - ``"vscode_generic"``       : Inside VS Code but not a known AI agent
      - ``"unknown"``              : Fallback
    """
    # ── Runtime env vars take priority (these are set by the
    #     host process at launch — more reliable than disk checks)

    explicit = (
        _os.environ.get("PAPER_SEARCH_MCP_CLIENT_HOST")
        or _os.environ.get("PAPER_SEARCH_MCP_MCP_HOST")
        or ""
    ).strip().lower()
    explicit = re.sub(r"[^a-z0-9]+", "_", explicit).strip("_")
    explicit_aliases = {
        "codex": "codex",
        "codex_app": "codex",
        "codex_desktop": "codex",
        "openai_codex": "codex",
        "openai_codex_desktop": "codex",
        "codex_vscode": "codex_vscode",
        "codex_ide": "codex_vscode",
        "openai_codex_ide": "codex_vscode",
        "openai_codex_vscode": "codex_vscode",
        "vscode_codex": "codex_vscode",
        "vs_code_codex": "codex_vscode",
        "claude_desktop": "claude_desktop",
        "claude_code": "claude_code_cli",
        "claude_code_cli": "claude_code_cli",
        "claude_code_vscode": "claude_code_vscode",
        "claude_code_desktop": "claude_code_desktop",
        "vscode": "vscode_generic",
        "vs_code": "vscode_generic",
        "vscode_generic": "vscode_generic",
        "dsh": "dsh",
        "deepseek": "dsh",
        "deepseek_harness": "dsh",
        "zcode": "zcode",
        "zhipu_code": "zcode",
        "codegeex": "zcode",
    }
    if explicit in explicit_aliases:
        return explicit_aliases[explicit]

    # Claude Code: CLAUDECODE=1 is always set at launch
    claudecode = _os.environ.get("CLAUDECODE", "")
    entrypoint = _os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if claudecode == "1":
        if entrypoint == "claude-vscode":
            return "claude_code_vscode"
        # ── Claude Code Desktop detection ──────────────────────────
        # When running as a standalone GUI app (not embedded in a
        # terminal), TERM is typically unset and stdin is not a TTY.
        # Also check for the explicit CLAUDE_CODE_DESKTOP sentinel.
        if (
            _os.environ.get("CLAUDE_CODE_DESKTOP")
            or (not _os.environ.get("TERM") and not _os.isatty(0))
        ):
            return "claude_code_desktop"
        return "claude_code_cli"

    # Claude Desktop (legacy): sets CLAUDE_DESKTOP at launch
    if _os.environ.get("CLAUDE_DESKTOP"):
        return "claude_desktop"

    if _os.environ.get("VSCODE_PID"):
        if _looks_like_codex_vscode_process():
            return "codex_vscode"
        return "vscode_generic"

    if _looks_like_codex_vscode_process():
        return "codex_vscode"

    # Harness-specific markers are lower-confidence diagnostics.  They are
    # checked after explicit Claude/VS Code signals and never grant Apps UI.
    if _looks_like_dsh():
        return "dsh"
    if _looks_like_zcode():
        return "zcode"

    # ── Disk-based detection: Codex always writes its
    #     global config to ~/.codex/config.toml ─────────
    codex_config = Path.home() / ".codex" / "config.toml"
    if codex_config.exists():
        return "codex"

    # ── Generic VS Code context ────────────────────────────
    if _os.environ.get("VSCODE_PID"):
        return "vscode_generic"

    return "unknown"


def _looks_like_dsh() -> bool:
    """Best-effort dsh signal used only for diagnostics and telemetry."""
    for key in ("DSH_CLIENT", "DSH_VERSION", "DSH_SESSION", "DEEPSEEK_HARNESS"):
        if _os.environ.get(key, "").strip():
            return True
    try:
        return (Path.home() / ".dsh").is_dir()
    except RuntimeError:
        return False


def _looks_like_zcode() -> bool:
    """Best-effort ZCode signal used only for diagnostics and telemetry."""
    integration = _os.environ.get("INTEGRATION_IDE", "").strip().lower()
    if integration in {"zcode", "zhipu", "codegeex"}:
        return True
    for key in ("ZCODE_VERSION", "ZCODE_SESSION", "ZHIPU_CODE"):
        if _os.environ.get(key, "").strip():
            return True
    try:
        return (Path.home() / ".zcode").is_dir()
    except RuntimeError:
        return False


def _looks_like_codex_vscode_process() -> bool:
    """Return True for the Codex VS Code extension process."""
    path = _os.environ.get("PATH", "").lower()
    if (
        "\\.vscode\\extensions\\openai.chatgpt-" in path
        or "/.vscode/extensions/openai.chatgpt-" in path
    ):
        return True
    for key in (
        "VSCODE_EXTENSION_ID",
        "VSCODE_EXTENSION_NAME",
        "VSCODE_IPC_HOOK_CLI",
        "TERM_PROGRAM",
    ):
        value = _os.environ.get(key, "").lower()
        if "openai.chatgpt" in value or "codex" in value:
            return True
    return False


# ===========================================================================
# MCP capability negotiation
# ===========================================================================

MCP_APPS_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_APPS_HTML_MIME = "text/html;profile=mcp-app"


def _model_to_dict(value: Any) -> Dict[str, Any]:
    """Convert an MCP/Pydantic model or mapping to a plain dict."""
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json", by_alias=True, exclude_none=True)
        except TypeError:
            dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from either an SDK model or a plain mapping."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def client_supports_mcp_apps(capabilities: Any) -> bool:
    """Return True only for an explicit MCP Apps capability declaration.

    Stable clients advertise the extension in ``capabilities.extensions``.
    Older ext-apps clients used ``experimental`` while the capability was
    still being standardized, so that location is accepted for compatibility.
    Host names and environment variables are deliberately ignored here.
    """
    caps = _model_to_dict(capabilities)
    for field in ("extensions", "experimental"):
        extensions = caps.get(field)
        if not isinstance(extensions, dict):
            continue
        extension = extensions.get(MCP_APPS_EXTENSION_ID)
        if not isinstance(extension, dict):
            continue
        mime_types = extension.get("mimeTypes")
        if mime_types is None:
            mime_types = extension.get("mime_types")
        if isinstance(mime_types, (list, tuple, set)) and MCP_APPS_HTML_MIME in mime_types:
            return True
    return False


def client_supports_elicitation_url(capabilities: Any) -> bool:
    """Return True when the wire-level ``elicitation.url`` field is present."""
    caps = _model_to_dict(capabilities)
    elicitation = caps.get("elicitation")
    if not isinstance(elicitation, dict) or "url" not in elicitation:
        return False
    # The valid declaration is often ``{"url": {}}``; an empty mapping is
    # still a capability and must not be tested with ``bool(value)``.
    return isinstance(elicitation.get("url"), dict)


def inspect_mcp_client(ctx: Any = None) -> Dict[str, Any]:
    """Extract initialize/request metadata for routing and diagnostics.

    FastMCP exposes the original initialize parameters as
    ``ctx.session.client_params``.  Newer clients may repeat capabilities in
    request ``_meta``; only the current request context is considered.  The
    returned values are plain JSON-compatible dictionaries so this helper is
    also usable in tests without importing a concrete SDK version.
    """
    result: Dict[str, Any] = {
        "capabilities": {},
        "source": "unavailable",
        "protocol_version": "",
        "client_info": {},
    }
    if ctx is None:
        return result

    try:
        session = _field(ctx, "session")
        params = _field(session, "client_params")
        if params is not None:
            capabilities = _model_to_dict(_field(params, "capabilities"))
            result.update(
                {
                    "capabilities": capabilities,
                    "source": "initialize",
                    "protocol_version": str(
                        _field(params, "protocolVersion", "")
                        or _field(params, "protocol_version", "")
                        or ""
                    ),
                    "client_info": _model_to_dict(
                        _field(params, "clientInfo")
                        or _field(params, "client_info")
                    ),
                }
            )

        # RequestParams.Meta permits extra fields.  Check both the Context
        # metadata and the raw request params for SDK implementations that do
        # not expose the alias uniformly.
        meta_values = []
        request_context = _field(ctx, "request_context")
        if request_context is not None:
            meta_values.append(_field(request_context, "meta"))
            request = _field(request_context, "request")
            request_params = _field(request, "params")
            if request_params is not None:
                meta_values.append(_field(request_params, "meta"))
                meta_values.append(_field(request_params, "_meta"))
        for raw_meta in meta_values:
            meta = _model_to_dict(raw_meta)
            for key in (
                "io.modelcontextprotocol/clientCapabilities",
                "io.modelcontextprotocol/client_capabilities",
            ):
                modern = meta.get(key)
                if isinstance(modern, dict):
                    result["capabilities"] = modern
                    result["source"] = "request_meta"
                    return result
    except Exception:
        # Capability detection must never make a normal tool call fail.
        return result
    return result


# Deprecated host allow-list names are retained as empty compatibility sets.
MCP_APPS_WIDGET_HOSTS = frozenset(
    {
        # ── Tentative MCP Apps hosts (2026-06) ──
        # These hosts MAY support MCP Apps sandbox widgets — we include
        # _meta on tool results AND also open a local_browser page as a
        # fallback.  If the widget renders, the user can use either.
        # If it doesn't, the browser page is already open.
    }
)

# Hosts KNOWN to definitely support MCP Apps sandbox widgets.
# For these hosts we use "app_only" mode — no local_browser fallback.
MCP_APPS_CONFIRMED_HOSTS = frozenset(
    {
    }
)

# Both constants are intentionally empty.  They remain only for callers that
# imported the legacy names; runtime routing uses inspect_mcp_client().


def host_supports_mcp_apps_widget(ctx: Any = None) -> bool:
    """Return runtime MCP Apps support when context is available.

    The no-argument form is retained for old integrations and fails closed.
    Rendering decisions must pass the current MCP context.
    """
    if ctx is not None:
        return client_supports_mcp_apps(inspect_mcp_client(ctx).get("capabilities"))
    # Static host sets are retained only for import compatibility and are not
    # evidence of a current MCP capability.
    return False


def host_mcp_apps_confirmed(ctx: Any = None) -> bool:
    """Return an explicit runtime capability, or a legacy diagnostic value."""
    if ctx is not None:
        return client_supports_mcp_apps(inspect_mcp_client(ctx).get("capabilities"))
    # Static host sets are retained only for import compatibility and are not
    # evidence of a current MCP capability.
    return False


def host_is_codex() -> bool:
    """Return True when running under a Codex surface with MCP Apps UI."""
    return detect_host() == "codex"


def host_is_vscode() -> bool:
    """Return True when running inside any VS Code window."""
    return detect_host() in ("codex_vscode", "claude_code_vscode", "vscode_generic")


def host_is_claude_code() -> bool:
    """Return True when running under Claude Code (any surface)."""
    return detect_host() in (
        "claude_code_vscode",
        "claude_code_cli",
        "claude_code_desktop",
    )


def vscode_binary() -> str:
    """Return the path to the ``code`` CLI binary, or empty string."""
    import shutil
    # Prefer the bundled binary inside VS Code's own install directory
    vscode_cwd = _os.environ.get("VSCODE_CWD", "")
    if vscode_cwd:
        for candidate in (
            Path(vscode_cwd) / "bin" / "code",
            Path(vscode_cwd) / "bin" / "code.cmd",
        ):
            if candidate.exists():
                    return str(candidate)
    return shutil.which("code") or ""


def _is_http_url(url: str) -> bool:
    return bool(re.match(r"^https?://", (url or "").strip(), flags=re.IGNORECASE))


def _open_url_with_system_browser(url: str) -> bool:
    """Deprecated server-side opener; navigation belongs to the client."""
    return False


def _open_url_with_vscode_open_url(url: str) -> bool:
    """Deprecated server-side VS Code opener."""
    return False


def _notify_vscode_companion(url: str) -> bool:
    """Deprecated IPC hook; the companion is opened explicitly by the client."""
    return False


def open_url_in_host_result(url: str) -> Dict[str, Any]:
    """Return a client-navigation instruction without opening a GUI server-side."""
    started = time.monotonic()
    target = (url or "").strip()
    try:
        host = detect_host()
    except Exception:
        host = "unknown"
    result: Dict[str, Any] = {
        "opened": False,
        "method": "",
        "host": host,
        "elapsed_ms": 0,
        "error": "",
    }
    try:
        if not target:
            result["error"] = "empty_url"
            return result

        if not _is_http_url(target):
            result["error"] = "unsupported_url"
            return result
        result["method"] = "client_open_link"
        result["error"] = "server_side_open_disabled"
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        return result
    finally:
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)


def open_url_in_host(url: str) -> bool:
    """Open a URL with the current nonblocking host strategy."""
    return bool(open_url_in_host_result(url).get("opened"))
