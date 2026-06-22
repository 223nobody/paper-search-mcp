# paper_search_mcp/ui/helpers.py
"""Widget schema builders and paper selection app helpers. No MCP deps."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import Field, create_model

from ..utils import DEFAULT_SAVE_PATH
from ..config import get_env
from ..engine.parse import (
    PAPER_SELECTION_WIDGET_URI, PAPER_SELECTION_WIDGET_TOOL,
    MINERU_KEY_WIDGET_URI, MINERU_KEY_WIDGET_TOOL,
    SELECTION_SEMANTICS_PARSE, SELECTION_SEMANTICS_DOWNLOAD_ONLY,
    _paper_selection_app_meta, _paper_selection_tool_meta,
    _paper_selection_app_payload, _paper_selection_app_prompt,
    _promote_paper_selection_app, _should_promote_paper_selection_app,
    _selection_semantics_name, _workflow_parse_execution_name,
    _mineru_api_key_configured, _mineru_key_app_meta, _mineru_key_setup_prompt,
    _build_paper_selection_schema, _parse_selected_indices,
    _parse_elicitation_selected_indices, _numbered_paper_fallback,
)

# Most widget helper functions are in engine/parse.py and re-exported here.
# This module adds UI-specific helpers not in engine/parse.py.

