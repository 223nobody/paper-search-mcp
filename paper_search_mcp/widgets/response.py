"""Helpers for returning MCP Apps widget results.

Previously, ``widget_tool_result`` wrapped the payload in a FastMCP
``ToolResult`` so the framework's ``to_mcp_result()`` would promote
``meta`` to ``CallToolResult._meta`` on the JSON-RPC wire.

However, FastMCP's auto-generated Pydantic output model rejects
``ToolResult`` instances because the model's ``result`` field expects a
plain dict.  Instead, we return a plain dict with ``_meta`` embedded as
a regular key.  MCP Apps hosts receive the widget metadata via the
``meta`` parameter on the ``@mcp.tool()`` decorator, which FastMCP
already promotes to ``CallToolResult._meta`` when ``structured_output=True``
is set.
"""

from __future__ import annotations

from typing import Any, Dict


def widget_tool_result(payload: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Return a plain dict with widget metadata embedded as ``_meta``.

    The ``@mcp.tool(meta={...}, structured_output=True)`` decorator on the
    calling tool already provides the widget resource URI to FastMCP, which
    promotes it to ``CallToolResult._meta``.  The ``_meta`` key carried in
    the returned dict is available to downstream consumers that inspect the
    JSON payload directly.
    """
    payload["_meta"] = dict(meta)
    return payload


def unwrap_tool_result(value: Any) -> Any:
    """Return the business payload from a dict or legacy result wrapper.

    Handles legacy FastMCP ToolResult and mcp.types.CallToolResult wrappers
    that may still be returned by some code paths or older server versions.
    Plain dicts are returned unchanged.
    """
    # FastMCP ToolResult (legacy path — kept for backward compatibility)
    try:
        from fastmcp.tools.base import ToolResult as _ToolResult
    except ImportError:
        _ToolResult = None  # type: ignore[assignment]

    if _ToolResult is not None and isinstance(value, _ToolResult):
        structured = getattr(value, "structured_content", None)
        if isinstance(structured, dict) and isinstance(structured.get("result"), dict):
            return structured["result"]
        return structured if structured is not None else {}

    # Legacy mcp.types.CallToolResult
    try:
        from mcp.types import CallToolResult as _CallToolResult
    except ImportError:
        _CallToolResult = None  # type: ignore[assignment]

    if _CallToolResult is not None and isinstance(value, _CallToolResult):
        structured = getattr(value, "structuredContent", None)
        if isinstance(structured, dict) and isinstance(structured.get("result"), dict):
            return structured["result"]
        return structured if structured is not None else {}

    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        return value["result"]
    return value
