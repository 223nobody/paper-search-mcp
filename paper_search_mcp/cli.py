#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

from .config import get_env
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.pmc import PMCSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.europepmc import EuropePMCSearcher
from .academic_platforms.dblp import DBLPSearcher
from .academic_platforms.openaire import OpenAiresearcher
from .academic_platforms.citeseerx import CiteSeerXSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.base_search import BASESearcher
from .academic_platforms.unpaywall import UnpaywallResolver, UnpaywallSearcher
from .academic_platforms.zenodo import ZenodoSearcher
from .academic_platforms.hal import HALSearcher
from .academic_platforms.ssrn import SSRNSearcher
from .cache import (
    delete_cache,
    get_cached_paths,
    list_assets,
    list_parsed,
    read_parsed,
    record_download,
    search_parsed,
)
from .parsers.mineru import mineru_health_check, parse_pdf_with_mineru
from .utils import DEFAULT_SAVE_PATH, resolve_save_path

# ---------------------------------------------------------------------------
# Searcher registry
# ---------------------------------------------------------------------------

SEARCHERS: Dict[str, Any] = {}


def _init_searchers() -> None:
    """Lazily initialize searcher instances."""
    if SEARCHERS:
        return

    SEARCHERS["arxiv"] = ArxivSearcher()
    SEARCHERS["pubmed"] = PubMedSearcher()
    SEARCHERS["biorxiv"] = BioRxivSearcher()
    SEARCHERS["medrxiv"] = MedRxivSearcher()
    SEARCHERS["google_scholar"] = GoogleScholarSearcher()
    SEARCHERS["iacr"] = IACRSearcher()
    SEARCHERS["semantic"] = SemanticSearcher()
    SEARCHERS["crossref"] = CrossRefSearcher()
    SEARCHERS["openalex"] = OpenAlexSearcher()
    SEARCHERS["pmc"] = PMCSearcher()
    SEARCHERS["core"] = CORESearcher()
    SEARCHERS["europepmc"] = EuropePMCSearcher()
    SEARCHERS["dblp"] = DBLPSearcher()
    SEARCHERS["openaire"] = OpenAiresearcher()
    SEARCHERS["citeseerx"] = CiteSeerXSearcher()
    SEARCHERS["doaj"] = DOAJSearcher()
    SEARCHERS["base"] = BASESearcher()
    unpaywall_resolver = UnpaywallResolver()
    SEARCHERS["unpaywall"] = UnpaywallSearcher(resolver=unpaywall_resolver)
    SEARCHERS["zenodo"] = ZenodoSearcher()
    SEARCHERS["hal"] = HALSearcher()
    SEARCHERS["ssrn"] = SSRNSearcher()

    # Optional paid connectors
    ieee_key = get_env("IEEE_API_KEY", "")
    if ieee_key:
        from .academic_platforms.ieee import IEEESearcher
        SEARCHERS["ieee"] = IEEESearcher()

    acm_key = get_env("ACM_API_KEY", "")
    if acm_key:
        from .academic_platforms.acm import ACMSearcher
        SEARCHERS["acm"] = ACMSearcher()


ALL_SOURCES = [
    "arxiv", "pubmed", "biorxiv", "medrxiv", "google_scholar", "iacr",
    "semantic", "crossref", "openalex", "pmc", "core", "europepmc",
    "dblp", "openaire", "citeseerx", "doaj", "base", "zenodo", "hal",
    "ssrn", "unpaywall",
]


def _parse_sources(sources: str) -> List[str]:
    if not sources or sources.strip().lower() == "all":
        return [s for s in ALL_SOURCES if s in SEARCHERS]
    normalized = [p.strip().lower() for p in sources.split(",") if p.strip()]
    return [s for s in normalized if s in SEARCHERS]


def _paper_unique_key(paper: Dict[str, Any]) -> str:
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = (paper.get("title") or "").strip().lower()
    authors = (paper.get("authors") or "").strip().lower()
    if title:
        return f"title:{title}|authors:{authors}"
    return f"id:{(paper.get('paper_id') or '').strip().lower()}"


def _dedupe(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: list[Dict[str, Any]] = []
    for p in papers:
        k = _paper_unique_key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _async_search(searcher: Any, query: str, max_results: int, **kwargs) -> List[Dict]:
    if kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [p.to_dict() for p in papers]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_search(args: argparse.Namespace) -> int:
    _init_searchers()
    selected = _parse_sources(args.sources)
    if not selected:
        print(json.dumps({"error": "No valid sources selected", "available": sorted(SEARCHERS.keys())}))
        return 1

    tasks = {}
    for src in selected:
        searcher = SEARCHERS[src]
        extra = {}
        if src == "semantic" and args.year:
            extra["year"] = args.year
        tasks[src] = _async_search(searcher, args.query, args.max_results, **extra)

    names = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    merged: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    source_counts: Dict[str, int] = {}

    for name, result in zip(names, results):
        if isinstance(result, Exception):
            errors[name] = str(result)
            source_counts[name] = 0
        else:
            source_counts[name] = len(result)
            for p in result:
                if not p.get("source"):
                    p["source"] = name
                merged.append(p)

    deduped = _dedupe(merged)

    output = {
        "query": args.query,
        "sources_used": names,
        "source_results": source_counts,
        "errors": errors,
        "total": len(deduped),
        "papers": deduped,
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


async def cmd_download(args: argparse.Namespace) -> int:
    _init_searchers()
    source = args.source.strip().lower()

    if source not in SEARCHERS:
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(SEARCHERS.keys())}))
        return 1

    searcher = SEARCHERS[source]
    try:
        save_path = resolve_save_path(args.save_path)
        result = await asyncio.to_thread(searcher.download_pdf, args.paper_id, save_path)
        if isinstance(result, str) and os.path.exists(result):
            record_download(
                pdf_path=result,
                source=source,
                paper_id=args.paper_id,
                downloader=f"{source}.download_pdf",
                legal_status="source_native_or_open_access",
            )
        print(json.dumps({"status": "ok", "path": result}))
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_read(args: argparse.Namespace) -> int:
    _init_searchers()
    source = args.source.strip().lower()

    if source not in SEARCHERS:
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(SEARCHERS.keys())}))
        return 1

    searcher = SEARCHERS[source]
    try:
        save_path = resolve_save_path(args.save_path)
        text = await asyncio.to_thread(searcher.read_paper, args.paper_id, save_path)
        print(text)
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_sources(args: argparse.Namespace) -> int:
    _init_searchers()
    print(json.dumps({"sources": sorted(SEARCHERS.keys())}, indent=2))
    return 0


async def cmd_parse(args: argparse.Namespace) -> int:
    result = await asyncio.to_thread(
        parse_pdf_with_mineru,
        args.pdf_path,
        paper_key_hint=args.paper_key or "",
        source=args.source or "",
        paper_id=args.paper_id or "",
        doi=args.doi or "",
        title=args.title or "",
        mode=args.mode,
        backend=args.backend or "",
        force=args.force,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in {"ok", "cached"} else 1


async def cmd_mineru_health(args: argparse.Namespace) -> int:
    result = await asyncio.to_thread(mineru_health_check, mode=args.mode, backend=args.backend or "")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


async def cmd_cache(args: argparse.Namespace) -> int:
    action = args.cache_command
    if action == "list":
        papers = list_parsed()
        print(json.dumps({"papers": papers, "total": len(papers)}, indent=2, ensure_ascii=False))
        return 0
    if action == "get":
        result = read_parsed(args.paper_key, args.format)
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if action == "assets":
        result = list_assets(args.paper_key, args.asset_type)
        print(json.dumps({"assets": result, "total": len(result)}, indent=2, ensure_ascii=False))
        return 0
    if action == "search":
        result = search_parsed(args.paper_key, args.query, args.max_results)
        print(json.dumps({"hits": result, "total": len(result)}, indent=2, ensure_ascii=False))
        return 0
    if action == "paths":
        result = get_cached_paths(args.paper_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if action == "delete":
        deleted = delete_cache(args.paper_key)
        print(json.dumps({"paper_key": args.paper_key, "deleted": deleted}, indent=2, ensure_ascii=False))
        return 0 if deleted else 1
    print(json.dumps({"error": f"Unknown cache command: {action}"}))
    return 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-search",
        description="Search, download, and read academic papers from 20+ sources.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Search for papers across academic platforms")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("-n", "--max-results", type=int, default=5, help="Max results per source (default: 5)")
    p_search.add_argument("-s", "--sources", default="all",
                          help="Comma-separated sources or 'all' (default: all)")
    p_search.add_argument("-y", "--year", default=None,
                          help="Year filter for Semantic Scholar (e.g. '2020', '2018-2022')")

    # download
    p_dl = sub.add_parser("download", help="Download a paper PDF")
    p_dl.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_dl.add_argument("paper_id", help="Paper identifier")
    p_dl.add_argument("-o", "--save-path", default=DEFAULT_SAVE_PATH, help=f"Save directory (default: {DEFAULT_SAVE_PATH})")

    # read
    p_read = sub.add_parser("read", help="Download and extract text from a paper")
    p_read.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_read.add_argument("paper_id", help="Paper identifier")
    p_read.add_argument("-o", "--save-path", default=DEFAULT_SAVE_PATH, help=f"Save directory (default: {DEFAULT_SAVE_PATH})")

    # sources
    sub.add_parser("sources", help="List available sources")

    # parse
    p_parse = sub.add_parser("parse", help="Parse a local PDF into cached Markdown/JSON/assets")
    p_parse.add_argument("pdf_path", help="Path to a local PDF")
    p_parse.add_argument("--paper-key", default="", help="Optional stable cache key")
    p_parse.add_argument("--source", default="", help="Source platform for provenance")
    p_parse.add_argument("--paper-id", default="", help="Source paper ID for provenance")
    p_parse.add_argument("--doi", default="", help="DOI for cache key/provenance")
    p_parse.add_argument("--title", default="", help="Paper title for cache key/provenance")
    p_parse.add_argument("--mode", default="auto", choices=["auto", "extract", "local_api", "cloud_api", "cli", "pypdf"],
                         help="Parser mode (default: auto)")
    p_parse.add_argument("--backend", default="", help="MinerU backend, e.g. pipeline/vlm/hybrid")
    p_parse.add_argument("--force", action="store_true", help="Re-parse even if cached")

    # mineru-health
    p_health = sub.add_parser("mineru-health", help="Check MinerU local API/CLI and pypdf fallback")
    p_health.add_argument("--mode", default="auto", choices=["auto", "extract", "local_api", "cloud_api", "cli", "pypdf"])
    p_health.add_argument("--backend", default="", help="MinerU backend, e.g. pipeline/vlm/hybrid")

    # cache
    p_cache = sub.add_parser("cache", help="Inspect parsed paper cache")
    cache_sub = p_cache.add_subparsers(dest="cache_command", required=True)
    cache_sub.add_parser("list", help="List cached parsed papers")

    p_cache_get = cache_sub.add_parser("get", help="Read cached parsed paper data")
    p_cache_get.add_argument("paper_key")
    p_cache_get.add_argument("-f", "--format", default="markdown",
                             choices=["markdown", "md", "json", "content_list", "manifest", "metadata", "paths"])

    p_cache_assets = cache_sub.add_parser("assets", help="List cached extracted assets")
    p_cache_assets.add_argument("paper_key")
    p_cache_assets.add_argument("-t", "--asset-type", default="all")

    p_cache_search = cache_sub.add_parser("search", help="Search cached parsed text")
    p_cache_search.add_argument("paper_key")
    p_cache_search.add_argument("query")
    p_cache_search.add_argument("-n", "--max-results", type=int, default=20)

    p_cache_paths = cache_sub.add_parser("paths", help="Show cache file paths")
    p_cache_paths.add_argument("paper_key")

    p_cache_delete = cache_sub.add_parser("delete", help="Delete one cached parsed paper")
    p_cache_delete.add_argument("paper_key")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "search": cmd_search,
        "download": cmd_download,
        "read": cmd_read,
        "sources": cmd_sources,
        "parse": cmd_parse,
        "mineru-health": cmd_mineru_health,
        "cache": cmd_cache,
    }

    exit_code = asyncio.run(dispatch[args.command](args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
