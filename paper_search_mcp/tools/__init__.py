# paper_search_mcp/tools/__init__.py
"""MCP tool registration functions.

Usage in server.py:
    from .tools.cache import register_cache_tools
    from .tools.parse import register_parse_tools
    from .tools.widgets import register_widget_tools
    from .tools.core import register_core_tools
    from .tools.orchestration import register_orchestration_tools
    from .tools.sources import register_source_tools
    from .tools.publisher import register_publisher_tools

    register_cache_tools(mcp)
    register_parse_tools(mcp)
    register_widget_tools(mcp)
    register_core_tools(mcp)
    register_orchestration_tools(mcp, _SEARCHERS)
    register_source_tools(mcp, _SEARCHERS)
    register_publisher_tools(mcp)
"""

from .cache import register_cache_tools
from .parse import register_parse_tools
from .widgets import register_widget_tools
from .core import register_core_tools
from .orchestration import register_orchestration_tools
from .sources import register_source_tools
from .publisher import register_publisher_tools
