# paper_search_mcp/ui/__init__.py
"""Local browser UI package.

Templates and HTTP server for paper selection and MinerU API key setup.
"""

from .html_templates import (
    PAPER_SELECTION_WIDGET_HTML,
    MINERU_KEY_WIDGET_HTML,
    _render_local_selection_html,
)
from .server import (
    _ensure_local_selection_server,
    _create_local_selection_page,
    _attach_local_selection_ui,
)
