from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from pypdf import PdfWriter

from paper_search_mcp import cache
from paper_search_mcp.parsers.mineru import parse_pdfs_with_mineru


def _make_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)


def _elapsed(fn, *args, **kwargs) -> tuple[Any, float]:
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - started


def _search_legacy(keys: List[str], query: str, cache_dir: str, max_results: int) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for key in keys:
        for hit in cache.search_parsed(key, query, max_results - len(hits), cache_dir=cache_dir):
            hit["paper_key"] = key
            hits.append(hit)
            if len(hits) >= max_results:
                return hits
    return hits


async def _maybe_search(query: str, sources: str, max_results: int) -> Dict[str, Any]:
    if not query:
        return {}
    from paper_search_mcp import server

    started = time.perf_counter()
    result = await server.search_papers(query, sources=sources, max_results_per_source=max_results)
    return {
        "query": query,
        "sources": result.get("sources_used", []),
        "total": result.get("total", 0),
        "raw_total": result.get("raw_total", 0),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "cache": result.get("cache", {}),
    }


async def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    cleanup = False
    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="paper_search_bench_"))
        cleanup = bool(args.cleanup)

    cache_dir = str((work_dir / ".paper_search_cache").resolve())
    pdf_dir = work_dir / "pdfs"
    pdfs = [pdf_dir / f"bench-{index + 1}.pdf" for index in range(args.pdf_count)]
    for pdf in pdfs:
        if args.force or not pdf.exists():
            _make_pdf(pdf)

    items = [
        {
            "pdf_path": str(pdf),
            "paper_key": f"bench-{index + 1}",
            "title": f"Benchmark Paper {index + 1}",
            "source": "benchmark",
            "paper_id": str(index + 1),
        }
        for index, pdf in enumerate(pdfs)
    ]

    parsed, parse_elapsed = _elapsed(
        parse_pdfs_with_mineru,
        items,
        mode=args.mode,
        cache_dir=cache_dir,
        force=args.force,
    )
    cached, cached_elapsed = _elapsed(
        parse_pdfs_with_mineru,
        items,
        mode=args.mode,
        cache_dir=cache_dir,
        force=False,
    )
    indexed, index_elapsed = _elapsed(cache.rebuild_parsed_index, cache_dir)
    fts_hits, fts_elapsed = _elapsed(cache.search_parsed_index, args.search_text, "", args.max_results, cache_dir)
    legacy_hits, legacy_elapsed = _elapsed(
        _search_legacy,
        [item["paper_key"] for item in items],
        args.search_text,
        cache_dir,
        args.max_results,
    )
    network_search = await _maybe_search(args.query, args.sources, args.max_results)

    result = {
        "status": "ok",
        "work_dir": str(work_dir),
        "cache_dir": cache_dir,
        "pdf_count": len(pdfs),
        "mode": args.mode,
        "timings_seconds": {
            "parse": round(parse_elapsed, 4),
            "cache_hit_parse": round(cached_elapsed, 4),
            "rebuild_index": round(index_elapsed, 4),
            "fts_search": round(fts_elapsed, 6),
            "legacy_search": round(legacy_elapsed, 6),
        },
        "speedups": {
            "cache_hit_vs_parse": round(parse_elapsed / cached_elapsed, 2) if cached_elapsed > 0 else None,
            "fts_vs_legacy_search": round(legacy_elapsed / fts_elapsed, 2) if fts_elapsed > 0 else None,
        },
        "parsed": {
            "ok": sum(1 for item in parsed if item.get("status") in {"ok", "cached"}),
            "statuses": [item.get("status") for item in parsed],
        },
        "cached": {
            "ok": sum(1 for item in cached if item.get("status") in {"ok", "cached"}),
            "statuses": [item.get("status") for item in cached],
        },
        "index": indexed,
        "hits": {
            "fts": len(fts_hits),
            "legacy": len(legacy_hits),
            "query": args.search_text,
        },
        "network_search": network_search,
    }

    if cleanup:
        shutil.rmtree(work_dir, ignore_errors=True)
        result["work_dir_removed"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark local paper-search parse/cache/index paths.")
    parser.add_argument("--pdf-count", type=int, default=8, help="Number of local sample PDFs to create.")
    parser.add_argument("--work-dir", default="", help="Reusable benchmark work directory. Defaults to a temp directory.")
    parser.add_argument("--mode", default="pypdf", choices=["auto", "extract", "local_api", "cloud_api", "cli", "pypdf"])
    parser.add_argument("--search-text", default="extractable", help="Text query for parsed-cache search benchmarks.")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--force", action="store_true", help="Force the first parse pass to reparse PDFs.")
    parser.add_argument("--cleanup", action="store_true", help="Remove temp work dir when --work-dir is not provided.")
    parser.add_argument("--query", default="", help="Optional live paper search query to benchmark.")
    parser.add_argument("--sources", default="fast", help="Sources for optional live search query.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(run_benchmark(args))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
