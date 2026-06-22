"""Helpers for returning MCP Apps widget results.

FastMCP structured output wraps plain dictionaries in ``structuredContent``.
Some hosts only render Apps when widget metadata is present on the top-level
tool result metadata, so render tools should return ``CallToolResult`` directly.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from mcp.types import CallToolResult, TextContent


def widget_tool_result(payload: Dict[str, Any], meta: Dict[str, Any]) -> CallToolResult:
    """Return a tool result with widget metadata at the MCP protocol top level."""
    return CallToolResult(
        _meta=dict(meta),
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False),
            )
        ],
        structuredContent={"result": payload},
    )


def unwrap_tool_result(value: Any) -> Any:
    """Return the business payload from either a raw dict or a CallToolResult."""
    if isinstance(value, CallToolResult):
        structured = value.structuredContent
        if isinstance(structured, dict) and isinstance(structured.get("result"), dict):
            return structured["result"]
        return structured if structured is not None else {}
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        return value["result"]
    return value
