"""Compatibility hooks for the optional VS Code companion.

The companion now opens an explicit selection URL through a single Webview.
The MCP server must not write temp files or create named pipes, so these
legacy functions intentionally report that no out-of-band bridge is present.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def notify_companion_extension(url: str) -> bool:
    """Deprecated no-op; callers should return the URL to the MCP client."""
    return False


def read_selection_result() -> Optional[Dict[str, Any]]:
    """Deprecated no-op; selection is submitted directly by the Webview page."""
    return None


def clear_pending() -> None:
    """Deprecated no-op retained for old integrations."""
    return None


def _write_named_pipe(request: Dict[str, Any]) -> bool:
    """Deprecated no-op retained so old imports fail closed without IPC."""
    return False
