"""Cache-related MCP tool registration.

Extracted from paper_search_mcp/server.py to keep tool definitions modular.
"""

import asyncio
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Helpers imported from the cache package
# ---------------------------------------------------------------------------
from ..cache import (
    cleanup_redundant_artifacts,
    cleanup_stale_cache_entries as cache_cleanup_stale_cache_entries,
    delete_cache,
    get_download_health,
    index_parsed_paper,
    list_assets,
    list_parsed,
    read_parsed,
    rebuild_parsed_index,
    resolved_parsed_paths,
    search_parsed_index,
)


# ===================================================================
# Registration entry-point
# ===================================================================


def register_cache_tools(mcp):
    """Register every cache-related MCP tool on the supplied FastMCP instance."""

    # ---------------------------------------------------------------
    # Parsed-paper index management
    # ---------------------------------------------------------------

    @mcp.tool()
    async def index_parsed_cache(paper_key: str = "") -> Dict[str, Any]:
        """Build or rebuild the parsed-paper SQLite FTS index."""
        if paper_key:
            return await asyncio.to_thread(index_parsed_paper, paper_key)
        return await asyncio.to_thread(rebuild_parsed_index)

    # ---------------------------------------------------------------
    # Listing / reading parsed papers
    # ---------------------------------------------------------------

    @mcp.tool()
    async def list_parsed_papers() -> Dict[str, Any]:
        """List cached parsed papers."""
        entries = await asyncio.to_thread(list_parsed)
        return {"papers": entries, "total": len(entries)}

    @mcp.tool()
    async def get_parsed_paper(paper_key: str, output_format: str = "markdown") -> Any:
        """Read cached parsed paper data as markdown, json, manifest, metadata, or paths."""
        return await asyncio.to_thread(read_parsed, paper_key, output_format)

    @mcp.tool()
    async def get_paper_assets(paper_key: str, asset_type: str = "all") -> List[Dict[str, str]]:
        """List cached extracted assets for a parsed paper."""
        return await asyncio.to_thread(list_assets, paper_key, asset_type)

    # ---------------------------------------------------------------
    # Searching parsed content
    # ---------------------------------------------------------------

    @mcp.tool()
    async def search_parsed_paper(paper_key: str, query: str, max_results: int = 20) -> Dict[str, Any]:
        """Search cached parsed Markdown/content blocks for a query string."""
        hits = await asyncio.to_thread(search_parsed_index, query, paper_key, max_results)
        return {"paper_key": paper_key, "query": query, "hits": hits, "total": len(hits), "index": "sqlite_fts_or_fallback"}

    @mcp.tool()
    async def search_parsed_papers(query: str, paper_key: str = "", max_results: int = 20) -> Dict[str, Any]:
        """Search the parsed-paper FTS index across one or all parsed papers."""
        hits = await asyncio.to_thread(search_parsed_index, query, paper_key, max_results)
        return {"paper_key": paper_key, "query": query, "hits": hits, "total": len(hits), "index": "sqlite_fts_or_fallback"}

    # ---------------------------------------------------------------
    # Deleting parsed cache
    # ---------------------------------------------------------------

    @mcp.tool()
    async def delete_parsed_cache(paper_key: str) -> Dict[str, Any]:
        """Delete cached parsed artifacts for one paper."""
        deleted = await asyncio.to_thread(delete_cache, paper_key)
        return {"paper_key": paper_key, "deleted": deleted}

    # ---------------------------------------------------------------
    # Paths
    # ---------------------------------------------------------------

    @mcp.tool()
    async def get_parsed_paths(paper_key: str) -> Dict[str, str]:
        """Return filesystem paths for metadata plus resolved Markdown, JSON, and assets."""
        return await asyncio.to_thread(resolved_parsed_paths, paper_key)

    # ---------------------------------------------------------------
    # Cache maintenance
    # ---------------------------------------------------------------

    @mcp.tool()
    async def cleanup_redundant_cache_artifacts(apply: bool = False) -> Dict[str, Any]:
        """Remove historical heavyweight cache duplicates; dry-run unless apply is true."""
        return await asyncio.to_thread(cleanup_redundant_artifacts, None, dry_run=not apply)

    @mcp.tool()
    async def cleanup_stale_cache_entries(apply: bool = False) -> Dict[str, Any]:
        """Remove parsed-paper indexes whose recorded PDF and parsed artifacts are missing."""
        return await asyncio.to_thread(cache_cleanup_stale_cache_entries, None, dry_run=not apply)

    # ---------------------------------------------------------------
    # Download health stats
    # ---------------------------------------------------------------

    @mcp.tool()
    async def get_download_health_stats() -> Dict[str, Any]:
        """Return persistent success/latency stats for download fallback methods."""
        return await asyncio.to_thread(get_download_health)
