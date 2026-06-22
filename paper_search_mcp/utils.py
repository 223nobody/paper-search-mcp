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

    # ── Disk-based detection: Codex always writes its
    #     global config to ~/.codex/config.toml ─────────
    codex_config = Path.home() / ".codex" / "config.toml"
    if codex_config.exists():
        return "codex"

    # ── Generic VS Code context ────────────────────────────
    if _os.environ.get("VSCODE_PID"):
        return "vscode_generic"

    return "unknown"


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


MCP_APPS_WIDGET_HOSTS = frozenset(
    {
        "codex",
        "claude_desktop",
        # ── Tentative MCP Apps hosts (2026-06) ──
        # These hosts MAY support MCP Apps sandbox widgets — we include
        # _meta on tool results AND also open a local_browser page as a
        # fallback.  If the widget renders, the user can use either.
        # If it doesn't, the browser page is already open.
        "claude_code_desktop",
        "claude_code_vscode",
    }
)

# Hosts KNOWN to definitely support MCP Apps sandbox widgets.
# For these hosts we use "app_only" mode — no local_browser fallback.
MCP_APPS_CONFIRMED_HOSTS = frozenset(
    {
        "codex",
        "claude_desktop",
        # ── claude_code_desktop intentionally omitted as of 2026-06 ──
        # Claude Code Desktop does NOT yet support MCP Apps sandbox
        # widgets.  It is kept in MCP_APPS_WIDGET_HOSTS for hybrid mode
        # (_meta is sent as future-proofing) but is NOT a confirmed
        # host, so the local_browser fallback is activated.
    }
)


def host_supports_mcp_apps_widget() -> bool:
    """Return True when the host might render MCP Apps sandboxed iframes.

    This is intentionally broader than ``host_mcp_apps_confirmed()``:
    tentative hosts return True so widget metadata is sent, but they still
    need local_browser fallback in hybrid mode.

    Supported hosts (as of 2026-06):
      - Codex Desktop (confirmed — native MCP Apps sandbox)
      - Claude Desktop (confirmed — legacy standalone app)
      - Claude Code Desktop (confirmed — standalone GUI WebView)
      - Claude Code VS Code extension (tentative — hybrid: widget + local_browser)

    NOT supported (no sandboxed iframe capability):
      - Codex VS Code plugin → uses localhost browser fallback
      - Claude Code CLI → uses numbered fallback or TUI
    """
    return detect_host() in MCP_APPS_WIDGET_HOSTS


def host_mcp_apps_confirmed() -> bool:
    """Return True when the host is KNOWN to definitely support MCP Apps.

    Confirmed hosts use ``app_only`` mode — no local_browser fallback.
    As of 2026-06 ONLY codex and claude_desktop are confirmed.
    Claude Code Desktop and VS Code are *tentative* hosts (in
    MCP_APPS_WIDGET_HOSTS but NOT confirmed) — hybrid mode is used:
    _meta is sent for future widget support, AND a local_browser page
    is opened as a working fallback.
    """
    return detect_host() in MCP_APPS_CONFIRMED_HOSTS


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
    """Ask the OS/default browser to open a URL without blocking the MCP tool."""
    import subprocess
    import sys
    import threading
    import webbrowser

    target = (url or "").strip()
    if not target:
        return False

    if _os.name == "nt":
        try:
            _os.startfile(target)  # type: ignore[attr-defined]
            return True
        except Exception:
            pass

    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["open", target],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return True
        except Exception:
            pass

    if _os.name == "posix":
        try:
            subprocess.Popen(
                ["xdg-open", target],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return True
        except Exception:
            pass

    result = {"opened": False}

    def _open() -> None:
        try:
            result["opened"] = bool(webbrowser.open(target, new=2, autoraise=True))
        except Exception:
            result["opened"] = False

    thread = threading.Thread(
        target=_open,
        name="paper-search-open-url",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=1.5)
    return bool(result["opened"] or thread.is_alive())


def _open_url_with_vscode_open_url(url: str) -> bool:
    """Best-effort VS Code URL opener. Never pass URLs as command arguments."""
    import subprocess

    code = vscode_binary()
    if not code:
        return False
    try:
        subprocess.Popen(
            [code, "--open-url", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def _notify_vscode_companion(url: str) -> bool:
    """Notify the optional VS Code companion via IPC without using code --command."""
    try:
        from .ui import vscode_bridge

        return vscode_bridge._write_named_pipe(
            {
                "action": "open_selection_page",
                "params": {"url": url},
            }
        )
    except Exception:
        return False


def open_url_in_host_result(url: str) -> Dict[str, Any]:
    """Open *url* and return details about the selected nonblocking strategy."""
    started = time.monotonic()
    target = (url or "").strip()
    host = detect_host()
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

        if host_is_vscode() and _notify_vscode_companion(target):
            result["opened"] = True
            result["method"] = "vscode_companion_ipc"
            return result

        tried_system_browser = False
        if _is_http_url(target):
            tried_system_browser = True
            if _open_url_with_system_browser(target):
                result["opened"] = True
                result["method"] = "system_browser"
                return result

        if host_is_vscode() and _open_url_with_vscode_open_url(target):
            result["opened"] = True
            result["method"] = "vscode_open_url"
            return result

        if not tried_system_browser and _open_url_with_system_browser(target):
            result["opened"] = True
            result["method"] = "system_browser"
            return result

        result["error"] = "no_opener_available"
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        return result
    finally:
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)


def open_url_in_host(url: str) -> bool:
    """Open a URL with the current nonblocking host strategy."""
    return bool(open_url_in_host_result(url).get("opened"))
