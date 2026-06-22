#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List

from .config import get_env
from .engine.paper import _dedupe_papers, _rank_papers_for_profile
from .engine.parse import AUTO_PARSE_SAVED_PDF_LIMIT, _parse_selected_indices
from .engine.search import (
    FAST_SOURCES as ENGINE_FAST_SOURCES,
    SEARCH_PROFILES as ENGINE_SEARCH_PROFILES,
    async_search,
    _env_float,
    _env_int,
    _parse_sources as _engine_parse_sources,
)
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
    cleanup_redundant_artifacts,
    cleanup_stale_cache_entries,
    delete_cache,
    get_download_health,
    index_parsed_paper,
    list_assets,
    list_parsed,
    read_parsed,
    rebuild_parsed_index,
    record_download,
    resolved_parsed_paths,
    search_parsed,
    search_parsed_index,
)
from .parsers.mineru import mineru_health_check, parse_pdf_with_mineru, parse_pdfs_with_mineru
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


ALL_SOURCES = list(dict.fromkeys(source for profile in ENGINE_SEARCH_PROFILES.values() for source in profile))
FAST_SOURCES = list(ENGINE_FAST_SOURCES)
SEARCH_PROFILES = ENGINE_SEARCH_PROFILES
SEARCH_TIMEOUT_ENV = "SEARCH_TIMEOUT_SECONDS"
SEARCH_SOURCE_TIMEOUT_ENV = "SEARCH_SOURCE_TIMEOUT_SECONDS"


def _parse_sources(sources: str) -> List[str]:
    return [source for source in _engine_parse_sources(sources) if source in SEARCHERS]


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


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = get_env(name, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


async def _search_source_with_timeout(
    source: str,
    operation: Any,
    timeout_seconds: float,
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        if timeout_seconds > 0:
            output = await asyncio.wait_for(operation, timeout=timeout_seconds)
        else:
            output = await operation
        return {
            "source": source,
            "output": output or [],
            "error": "",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except asyncio.TimeoutError:
        return {
            "source": source,
            "output": [],
            "error": f"timed out after {timeout_seconds:g}s",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "timed_out": True,
        }
    except Exception as exc:
        return {
            "source": source,
            "output": [],
            "error": str(exc),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_search(args: argparse.Namespace) -> int:
    from . import server

    output = await server.search_papers(
        query=args.query,
        max_results_per_source=args.max_results,
        sources=args.sources,
        year=args.year,
    )
    print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
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


async def cmd_parse_batch(args: argparse.Namespace) -> int:
    items = [{"pdf_path": path} for path in args.pdf_paths]
    results = await asyncio.to_thread(
        parse_pdfs_with_mineru,
        items,
        mode=args.mode,
        backend=args.backend or "",
        force=args.force,
    )
    parsed = sum(1 for result in results if result.get("status") in {"ok", "cached"})
    payload = {
        "status": "ok" if parsed == len(results) else "partial" if parsed else "failed",
        "results": results,
        "total": len(results),
        "parsed": parsed,
        "failed": len(results) - parsed,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if parsed == len(results) else 1


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
    if action == "search-index":
        result = search_parsed_index(args.query, args.paper_key or "", args.max_results)
        print(json.dumps({"hits": result, "total": len(result)}, indent=2, ensure_ascii=False))
        return 0
    if action == "index":
        result = index_parsed_paper(args.paper_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") == "ok" else 1
    if action == "rebuild-index":
        result = rebuild_parsed_index()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") in {"ok", "partial"} else 1
    if action == "download-health":
        result = get_download_health()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if action == "paths":
        result = resolved_parsed_paths(args.paper_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if action == "delete":
        deleted = delete_cache(args.paper_key)
        print(json.dumps({"paper_key": args.paper_key, "deleted": deleted}, indent=2, ensure_ascii=False))
        return 0 if deleted else 1
    if action == "cleanup-redundant":
        result = cleanup_redundant_artifacts(dry_run=not args.apply)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if action == "cleanup-stale":
        result = cleanup_stale_cache_entries(dry_run=not args.apply)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(json.dumps({"error": f"Unknown cache command: {action}"}))
    return 1


async def cmd_workflow(args: argparse.Namespace) -> int:
    """Full pipeline: search → select → download → parse.

    When more than 10 papers are requested, a terminal-based numbered
    selection is shown so the user can pick which papers to download.
    """
    _init_searchers()

    # ── Phase 1: Search ────────────────────────────────────────────────
    sources_list = _parse_sources(args.sources or "")
    if not sources_list:
        print(json.dumps({"error": "No valid sources selected."}))
        return 1

    print(f"Searching {len(sources_list)} sources for: {args.query}")
    per_source = max(1, args.max_results)
    overall_timeout = _env_float("SEARCH_TIMEOUT_SECONDS", 18.0, minimum=0.0)
    source_timeout = _env_float("SEARCH_SOURCE_TIMEOUT_SECONDS", 12.0, minimum=0.0)

    async def _search_one(source: str):
        searcher = SEARCHERS.get(source)
        if searcher is None:
            return source, []
        try:
            kwargs = {}
            if source == "arxiv":
                kwargs = {"sort_by": "relevance", "sort_order": "descending",
                          "timeout": _env_float("ARXIV_TIMEOUT_SECONDS", 8.0, minimum=1.0),
                          "max_attempts": _env_int("ARXIV_MAX_ATTEMPTS", 2, minimum=1)}
            elif source == "pubmed":
                kwargs = {"sort": "relevance"}
            elif source == "semantic" and args.year:
                kwargs = {"year": args.year}
            elif source == "iacr":
                kwargs = {"fetch_details": False}
            result = await async_search(searcher, args.query, per_source, **kwargs)
            papers = result if isinstance(result, list) else []
            for paper in papers:
                if not paper.get("source"):
                    paper["source"] = source
            return source, papers
        except Exception:
            return source, []

    tasks = [asyncio.create_task(_search_one(s)) for s in sources_list]
    try:
        if overall_timeout > 0:
            source_outputs = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=overall_timeout
            )
        else:
            source_outputs = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.TimeoutError:
        for t in tasks:
            if not t.done():
                t.cancel()
        source_outputs = [t.result() if t.done() and not t.cancelled() else (s, [])
                          for s, t in zip(sources_list, tasks)]

    merged: List[Dict[str, Any]] = []
    source_results: Dict[str, int] = {}
    for item in source_outputs:
        if isinstance(item, Exception):
            continue
        src, papers = item
        source_results[src] = len(papers)
        merged.extend(papers)

    all_papers = _dedupe_papers(merged, query=args.query)

    # ── Phase 2: Ranking ───────────────────────────────────────────────
    if args.ranking_profile:
        all_papers = _rank_papers_for_profile(
            all_papers, ranking_profile=args.ranking_profile, query=args.query
        )

    total = len(all_papers)
    if total == 0:
        print(json.dumps({"status": "no_results", "query": args.query}))
        return 1

    # ── Phase 3: Selection ─────────────────────────────────────────────
    requested = max(1, int(args.count or 5))
    limit = min(requested, total)

    if limit > AUTO_PARSE_SAVED_PDF_LIMIT:
        print(f"\nFound {total} papers. Requested {limit} (> {AUTO_PARSE_SAVED_PDF_LIMIT}).")
        for i, paper in enumerate(all_papers[:limit], 1):
            title = (paper.get("title") or "Untitled")[:110]
            src = paper.get("source", "?")
            doi = paper.get("doi", "")
            year = paper.get("published_date", paper.get("year", ""))[:10]
            print(f"  [{i:2d}] [{src:12s}] {title}")
            if doi:
                print(f"        DOI: {doi}  |  {year}")
        print("\nEnter comma-separated numbers, a range (e.g. 2-5), or 'all':")
        try:
            selection = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSelection cancelled.")
            return 1
        try:
            selected = _parse_selected_indices(selection or "all", limit)
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            return 1
    else:
        selected = list(range(1, limit + 1))
        print(f"\nAuto-selecting all {limit} papers (≤ {AUTO_PARSE_SAVED_PDF_LIMIT}).")

    if not selected:
        print("No papers selected.")
        return 1

    # ── Phase 4: Download ──────────────────────────────────────────────
    save_path = resolve_save_path(args.save_path)
    os.makedirs(save_path, exist_ok=True)
    dl_concurrency = args.download_concurrency if args.download_concurrency > 0 else 4
    semaphore = asyncio.Semaphore(dl_concurrency)

    downloaded: List[Dict[str, Any]] = []

    async def _download_one(index: int):
        async with semaphore:
            paper = all_papers[index - 1]
            source = paper.get("source", "").lower()
            paper_id = paper.get("paper_id", "")
            title = paper.get("title", "Untitled")[:80]
            searcher = SEARCHERS.get(source)
            if searcher is None:
                return {"index": index, "status": "skipped",
                        "message": f"No searcher for source {source}"}
            try:
                print(f"  [{index}] Downloading: {title}...")
                result = await asyncio.to_thread(searcher.download_pdf, paper_id, save_path)
                if isinstance(result, str) and os.path.exists(result):
                    record_download(
                        pdf_path=result, source=source, paper_id=paper_id,
                        downloader=f"{source}.download_pdf",
                        legal_status="source_native_or_open_access",
                    )
                    print(f"  [{index}] OK: {os.path.basename(result)}")
                    return {"index": index, "status": "downloaded",
                            "pdf_path": result, "source": source,
                            "paper_id": paper_id}
                print(f"  [{index}] FAILED: {result}")
                return {"index": index, "status": "failed",
                        "message": str(result)[:200]}
            except Exception as exc:
                print(f"  [{index}] ERROR: {exc}")
                return {"index": index, "status": "failed", "message": str(exc)[:200]}

    print(f"\nDownloading {len(selected)} paper(s) (concurrency={dl_concurrency})...")
    dl_results = await asyncio.gather(*[_download_one(i) for i in selected])
    downloaded = [r for r in dl_results if r.get("status") == "downloaded"]
    print(f"\nDownloaded: {len(downloaded)}  Failed: {len(dl_results) - len(downloaded)}")

    if not downloaded:
        return 1

    # ── Phase 5: Parse ─────────────────────────────────────────────────
    if args.no_parse:
        print("Skipping parse (--no-parse).")
        return 0

    parse_count = len(downloaded)
    if parse_count > AUTO_PARSE_SAVED_PDF_LIMIT:
        print(f"\n{parse_count} PDFs ready (> {AUTO_PARSE_SAVED_PDF_LIMIT}).")
        print("Enter comma-separated numbers to parse, a range, or 'all':")
        for i, d in enumerate(downloaded, 1):
            pdf = os.path.basename(d.get("pdf_path", ""))
            print(f"  [{i:2d}] {pdf}")
        try:
            sel = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nParse skipped.")
            return 0
        try:
            parse_indices = _parse_selected_indices(sel or "all", parse_count)
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            return 1
        to_parse = [downloaded[i - 1] for i in parse_indices]
    else:
        to_parse = downloaded

    if not to_parse:
        print("No PDFs selected for parsing.")
        return 0

    parse_items = [
        {"pdf_path": d["pdf_path"], "source": d.get("source", ""),
         "paper_id": d.get("paper_id", ""), "doi": "", "title": ""}
        for d in to_parse
    ]
    print(f"\nParsing {len(to_parse)} PDF(s) with MinerU (mode={args.parse_mode})...")
    parse_results = await asyncio.to_thread(
        parse_pdfs_with_mineru, parse_items, mode=args.parse_mode, backend="", force=False
    )
    parsed = sum(1 for r in parse_results if r.get("status") in {"ok", "cached"})
    failed = len(parse_results) - parsed
    print(f"Parsed: {parsed}  Failed: {failed}")
    return 0 if failed == 0 else 1


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
    p_search.add_argument("-s", "--sources", default="",
                          help="Comma-separated sources or profile fast/deep/all (default: PAPER_SEARCH_MCP_SEARCH_PROFILE or fast)")
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

    # parse-batch
    p_parse_batch = sub.add_parser("parse-batch", help="Parse multiple local PDFs, using MinerU extract batch when available")
    p_parse_batch.add_argument("pdf_paths", nargs="+", help="Path(s) to local PDFs")
    p_parse_batch.add_argument("--mode", default="auto", choices=["auto", "extract", "local_api", "cloud_api", "cli", "pypdf"],
                               help="Parser mode (default: auto)")
    p_parse_batch.add_argument("--backend", default="", help="MinerU backend, e.g. pipeline/vlm/hybrid")
    p_parse_batch.add_argument("--force", action="store_true", help="Re-parse even if cached")

    # mineru-health
    p_health = sub.add_parser("mineru-health", help="Check MinerU API key setup and pypdf fallback")
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

    p_cache_search_index = cache_sub.add_parser("search-index", help="Search the parsed-paper SQLite FTS index")
    p_cache_search_index.add_argument("query")
    p_cache_search_index.add_argument("--paper-key", default="", help="Optional paper key filter")
    p_cache_search_index.add_argument("-n", "--max-results", type=int, default=20)

    p_cache_index = cache_sub.add_parser("index", help="Index one parsed paper into SQLite FTS")
    p_cache_index.add_argument("paper_key")

    cache_sub.add_parser("rebuild-index", help="Rebuild the parsed-paper SQLite FTS index")
    cache_sub.add_parser("download-health", help="Show persistent download fallback health stats")

    p_cache_paths = cache_sub.add_parser("paths", help="Show cache file paths")
    p_cache_paths.add_argument("paper_key")

    p_cache_delete = cache_sub.add_parser("delete", help="Delete one cached parsed paper")
    p_cache_delete.add_argument("paper_key")

    p_cache_cleanup = cache_sub.add_parser(
        "cleanup-redundant",
        help="Remove historical heavyweight cache duplicates; dry-run by default",
    )
    p_cache_cleanup.add_argument("--apply", action="store_true", help="Actually delete redundant files")

    p_cache_cleanup_stale = cache_sub.add_parser(
        "cleanup-stale",
        help="Remove cache indexes whose PDF and parsed artifacts are missing; dry-run by default",
    )
    p_cache_cleanup_stale.add_argument("--apply", action="store_true", help="Actually delete stale cache indexes")

    # workflow
    p_wf = sub.add_parser("workflow", help="Full pipeline: search → select → download → parse")
    p_wf.add_argument("query", help="Search query string")
    p_wf.add_argument("-n", "--count", type=int, default=5, help="Number of papers to download (default: 5)")
    p_wf.add_argument("-m", "--max-results", type=int, default=5, help="Max results per source (default: 5)")
    p_wf.add_argument("-s", "--sources", default="", help="Comma-separated sources or profile name (default: fast)")
    p_wf.add_argument("-y", "--year", default=None, help="Optional year filter (Semantic Scholar only)")
    p_wf.add_argument("-o", "--save-path", default=DEFAULT_SAVE_PATH, help=f"Save directory (default: {DEFAULT_SAVE_PATH})")
    p_wf.add_argument("--ranking-profile", default="", help="e.g. 'agent-skill' for LLM/skill/library topics")
    p_wf.add_argument("--parse-mode", default="auto",
                       choices=["auto", "extract", "local_api", "cloud_api", "cli", "pypdf"],
                       help="MinerU parser mode (default: auto)")
    p_wf.add_argument("--no-parse", action="store_true", help="Skip MinerU parsing")
    p_wf.add_argument("--download-concurrency", type=int, default=0,
                       help="Concurrent downloads (default: 4)")

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
        "parse-batch": cmd_parse_batch,
        "mineru-health": cmd_mineru_health,
        "cache": cmd_cache,
        "workflow": cmd_workflow,
    }

    exit_code = asyncio.run(dispatch[args.command](args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
