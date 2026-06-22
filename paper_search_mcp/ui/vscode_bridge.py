# paper_search_mcp/ui/vscode_bridge.py
"""IPC bridge between paper-search-mcp and the VS Code companion extension.

When the companion extension (``paper-search-companion``) is installed, the
MCP server writes a pending-selection URL to a well-known temp file that the
extension polls.  The extension opens a Webview Panel, the user picks papers,
and the extension writes the result back to another temp file.

When the companion is *not* installed, callers fall back to ``code --open-url``
or the system browser — see ``open_url_in_host`` in ``..utils``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


_PENDING_URL_FILE = Path(tempfile.gettempdir()) / "paper_search_mcp_pending_url.json"
_RESULT_FILE = Path(tempfile.gettempdir()) / "paper_search_mcp_selection_result.json"


# ── Write side (MCP server → extension) ──────────────────────


def notify_companion_extension(url: str) -> bool:
    """Write a pending selection URL for the companion extension to pick up.

    Returns True if the file was written successfully.
    """
    try:
        _PENDING_URL_FILE.write_text(
            json.dumps({"url": url, "timestamp": _now_ms()}, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


# ── Read side (MCP server polls for result) ──────────────────


def read_selection_result() -> Optional[Dict[str, Any]]:
    """Read the selection result written by the companion extension.

    Returns ``None`` when no result is available yet.  The result file is
    deleted after a successful read.
    """
    try:
        if not _RESULT_FILE.exists():
            return None
        raw = _RESULT_FILE.read_text(encoding="utf-8")
        _RESULT_FILE.unlink(missing_ok=True)
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None


def clear_pending() -> None:
    """Delete the pending URL file (cleanup)."""
    _PENDING_URL_FILE.unlink(missing_ok=True)


# ── helpers ──────────────────────────────────────────────────


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


# ── Windows named-pipe writer (optional: direct trigger) ─────


def _write_named_pipe(request: Dict[str, Any]) -> bool:
    """Try to send a request directly to the companion extension via named pipe.

    Returns True on success, False when the pipe is not available.
    """
    if os.name != "nt":
        return False
    pipe_name = r"\\.\pipe\paper_search_mcp_selection"
    data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    try:
        with open(pipe_name, "wb", buffering=0) as pipe:
            pipe.write(data)
        return True
    except OSError:
        pass
    try:
        import win32file

        handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        try:
            win32file.WriteFile(handle, data)
        finally:
            win32file.CloseHandle(handle)
        return True
    except Exception:
        return False
