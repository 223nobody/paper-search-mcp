"""Register all 62 per-source search/download/read MCP tools.

Usage: register_source_tools(mcp, searchers)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import DEFAULT_SAVE_PATH, extract_doi, resolve_save_path
from ..config import get_env
from ..engine.search import async_search, _env_int, _env_float
from ..engine.download import (
    _download_source_pdf, _read_source_paper, _invalid_mcp_save_path,
)
from ..engine.paper import _paper_parse_candidate
from ..engine.download import _find_existing_pdf
from ..engine.parse import _after_saved_pdf, _workflow_parse_execution_name

# Source definitions: (source_key, DisplayName, has_special_params)
SEARCH_SOURCES = [
    ("arxiv", "arXiv"),
    ("pubmed", "PubMed"),
    ("biorxiv", "bioRxiv"),
    ("medrxiv", "medRxiv"),
    ("google_scholar", "Google Scholar"),
    # iacr is registered in _register_special_tools with extra params
    ("semantic", "Semantic Scholar"),
    ("crossref", "CrossRef"),
    ("openalex", "OpenAlex"),
    ("pmc", "PubMed Central"),
    ("core", "CORE"),
    ("europepmc", "Europe PMC"),
    ("dblp", "dblp"),
    ("openaire", "OpenAIRE"),
    ("citeseerx", "CiteSeerX"),
    ("doaj", "DOAJ"),
    ("base", "BASE"),
    ("zenodo", "Zenodo"),
    ("hal", "HAL"),
    ("ssrn", "SSRN"),
    ("unpaywall", "Unpaywall"),
]

DOWNLOAD_SOURCES = [
    "arxiv", "pubmed", "biorxiv", "medrxiv", "iacr", "semantic",
    "crossref", "openalex", "pmc", "core", "europepmc", "dblp",
    "openaire", "citeseerx", "doaj", "base", "zenodo", "hal", "ssrn",
]

READ_SOURCES = [
    "arxiv", "pubmed", "biorxiv", "medrxiv", "iacr", "semantic",
    "crossref", "openalex", "pmc", "core", "europepmc", "dblp",
    "openaire", "citeseerx", "doaj", "base", "zenodo", "hal", "ssrn",
]


def register_source_tools(mcp, searchers: Dict[str, Any]):
    """Register per-source search, download, and read tools on *mcp*."""

    # ---- SEARCH TOOLS ----
    for source, display_name in SEARCH_SOURCES:
        searcher = searchers.get(source)
        if searcher is None:
            continue

        # Build tool-specific params
        if source == "arxiv":
            @mcp.tool()
            async def search_arxiv(query: str, max_results: int = 10,
                                   sort_by: str = 'relevance',
                                   sort_order: str = 'descending') -> List[Dict]:
                """Search academic papers from arXiv."""
                kwargs = dict(sort_by=sort_by, sort_order=sort_order,
                             timeout=_env_float('ARXIV_TIMEOUT_SECONDS', 8.0, minimum=1.0),
                             max_attempts=_env_int('ARXIV_MAX_ATTEMPTS', 2, minimum=1))
                papers = await async_search(searchers["arxiv"], query, max_results, **kwargs)
                return papers if papers else []
        elif source == "pubmed":
            @mcp.tool()
            async def search_pubmed(query: str, max_results: int = 10,
                                    sort: str = 'relevance') -> List[Dict]:
                """Search academic papers from PubMed."""
                papers = await async_search(searchers["pubmed"], query, max_results, sort=sort)
                return papers if papers else []
        elif source == "semantic":
            @mcp.tool()
            async def search_semantic(query: str, year: Optional[str] = None,
                                      max_results: int = 10) -> List[Dict]:
                """Search academic papers from Semantic Scholar."""
                kwargs = {'year': year} if year is not None else {}
                papers = await async_search(searchers["semantic"], query, max_results, **kwargs)
                return papers if papers else []
        elif source == "crossref":
            @mcp.tool()
            async def search_crossref(query: str, max_results: int = 10,
                                      filter: Optional[str] = None,
                                      sort: Optional[str] = None,
                                      order: Optional[str] = None) -> List[Dict]:
                """Search academic papers from CrossRef."""
                kwargs = {k: v for k, v in {'filter': filter, 'sort': sort, 'order': order}.items() if v is not None}
                papers = await async_search(searchers["crossref"], query, max_results, **kwargs)
                return papers if papers else []
        else:
            # Generic search registration for other sources
            _make_search_tool(mcp, searchers, source, display_name)

    # ---- DOWNLOAD TOOLS ----
    for source in DOWNLOAD_SOURCES:
        searcher = searchers.get(source)
        if searcher is None:
            continue
        _make_download_tool(mcp, searchers, source)

    # ---- READ TOOLS ----
    for source in READ_SOURCES:
        searcher = searchers.get(source)
        if searcher is None:
            continue
        _make_read_tool(mcp, searchers, source)

    # ---- SPECIAL TOOLS ----
    _register_special_tools(mcp, searchers)


def _make_search_tool(mcp, searchers, source, display_name):
    """Register a generic search tool for a source."""
    searcher = searchers[source]

    tool_name = f"search_{source}"
    tool_doc = f"Search academic papers from {display_name}."

    @mcp.tool(name=tool_name)
    async def _search(query: str, max_results: int = 10) -> List[Dict]:
        papers = await async_search(searcher, query, max_results)
        return papers if papers else []

    _search.__doc__ = tool_doc
    return _search


def _make_download_tool(mcp, searchers, source):
    """Register a download tool for a source."""
    searcher = searchers[source]

    tool_name = f"download_{source}"
    tool_doc = f"Download PDF for a paper from {source.upper()}."

    @mcp.tool(name=tool_name)
    async def _download(paper_id: str, save_path: str = DEFAULT_SAVE_PATH) -> Any:
        return await _download_source_pdf(
            {source: searcher}, source=source, paper_id=paper_id, save_path=save_path,
        )

    _download.__doc__ = tool_doc
    return _download


def _make_read_tool(mcp, searchers, source):
    """Register a read tool for a source."""
    searcher = searchers[source]

    tool_name = f"read_{source}_paper"
    tool_doc = f"Read and extract text content from a {source.upper()} paper."

    @mcp.tool(name=tool_name)
    async def _read(paper_id: str, save_path: str = DEFAULT_SAVE_PATH) -> Any:
        return await _read_source_paper(
            searcher, source=source, paper_id=paper_id, save_path=save_path,
        )

    _read.__doc__ = tool_doc
    return _read


def _register_special_tools(mcp, searchers):
    """Register special tools that don't fit the standard pattern."""
    crossref = searchers.get("crossref")

    @mcp.tool()
    async def get_crossref_paper_by_doi(doi: str) -> Dict:
        """Get a specific paper from CrossRef by its DOI."""
        if crossref:
            paper = await asyncio.to_thread(crossref.get_paper_by_doi, doi)
            return paper.to_dict() if paper else {}
        return {}

    @mcp.tool()
    async def download_scihub(
        identifier: str,
        save_path: str = DEFAULT_SAVE_PATH,
        base_url: str = "https://sci-hub.se",
    ) -> Any:
        """Download paper PDF via Sci-Hub (optional fallback)."""
        from ..academic_platforms.sci_hub import SciHubFetcher
        invalid = _invalid_mcp_save_path(save_path)
        if invalid:
            return invalid
        save_path = resolve_save_path(save_path)
        fetcher = SciHubFetcher(base_url=base_url, output_dir=save_path)
        result = await asyncio.to_thread(fetcher.download_pdf, identifier)
        if result:
            return await _after_saved_pdf(
                result, source="scihub", paper_id=identifier,
                doi=extract_doi(identifier), title=identifier,
                save_path=save_path, downloader="scihub",
                legal_status="user_opt_in_scihub",
            )
        return "Sci-Hub download failed."

    @mcp.tool()
    async def search_iacr(query: str, max_results: int = 10,
                          fetch_details: bool = True) -> List[Dict]:
        """Search academic papers from IACR ePrint Archive."""
        iacr = searchers.get("iacr")
        if iacr:
            papers = await asyncio.to_thread(iacr.search, query, max_results, fetch_details)
            return [p.to_dict() for p in papers] if papers else []
        return []

