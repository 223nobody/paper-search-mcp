# paper_search_mcp/engine/download.py
"""
Core download engine functions extracted from server.py.

Provides:
- Save-path validation and wrapping (_invalid_mcp_save_path, _wrap_save_path_methods)
- PDF validation and result metadata (_is_valid_pdf_file, _pdf_result_metadata)
- Download candidate helpers (_candidate_download_id, _existing_pdf_candidates, _find_existing_pdf)
- Core download functions (_download_from_url, _download_with_fallback_path, _race_oa_downloads)
- Per-source download orchestration (_download_source_pdf, _read_source_paper)
- Session-paper download helpers (_download_selected_session_paper, _resolve_session_paper_pdf)
- Post-download processing (_after_saved_pdf, _after_saved_pdfs)
- PDF tracking helpers (_snapshot_pdf_files, _changed_pdf_paths, _recent_saved_pdf_papers)
- Download metadata builders (_downloaded_pdf_paper, _paper_from_download_metadata, _downloaded_pdf_papers)

No MCP dependencies. Searcher instances are injected via parameters.
Extracted from server.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from ..cache import (
    find_download_by_pdf_path,
    rank_download_methods,
    record_download,
    record_download_health,
    sha256_file,
)
from ..config import get_env
from ..utils import DEFAULT_SAVE_PATH, extract_doi, resolve_save_path
from .paper import (
    _canonical_pdf_stem,
    _extract_arxiv_id,
    _looks_like_pdf_path,
    _paper_field,
    _paper_parse_candidate,
    _paper_value,
    _paper_year,
    _paper_publication_date,
    _paper_publication_venue,
    _paper_original_url,
    _paper_doi,
    _pdf_filename_from_hint,
    _repository_paper_matches_request,
    _safe_filename,
    _searcher_for_source,
    _source_from_identifier,
)
from .search import _env_int, _env_float

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_ENV = "DOWNLOAD_TIMEOUT_SECONDS"
DOWNLOAD_STRATEGY_ENV = "DOWNLOAD_STRATEGY"
DOWNLOAD_CONCURRENCY_ENV = "DOWNLOAD_CONCURRENCY"
DOWNLOAD_MAX_RETRIES_ENV = "DOWNLOAD_MAX_RETRIES"
DOWNLOAD_RETRY_BACKOFF_ENV = "DOWNLOAD_RETRY_BACKOFF_SECONDS"
PAPER_FETCH_FALLBACK_ENV = "PAPER_FETCH_PDF_FALLBACK"
LIBGEN_ENABLED_ENV = "LIBGEN_ENABLED"
LIBGEN_BASE_URL_ENV = "LIBGEN_BASE_URL"
SEARCH_SOURCE_TIMEOUT_ENV = "SEARCH_SOURCE_TIMEOUT_SECONDS"
SAVED_PDF_BATCH_PROMPT_ENV = "SAVED_PDF_BATCH_PROMPT"
SAVED_PDF_BATCH_WINDOW_ENV = "SAVED_PDF_BATCH_WINDOW_SECONDS"

AUTO_PARSE_SAVED_PDF_LIMIT = 10

ALLOW_CUSTOM_SAVE_PATH_ENV = "ALLOW_CUSTOM_SAVE_PATH"
REQUIRE_EXPLICIT_SAVE_PATH_ENV = "REQUIRE_EXPLICIT_SAVE_PATH"


# ---------------------------------------------------------------------------
# Save-path policy
# ---------------------------------------------------------------------------

def _custom_save_paths_allowed() -> bool:
    return _env_flag_enabled(ALLOW_CUSTOM_SAVE_PATH_ENV, default="true")


def _explicit_save_path_required() -> bool:
    return _env_flag_enabled(REQUIRE_EXPLICIT_SAVE_PATH_ENV, default="true")


def _download_strategy(strategy: str = "") -> str:
    """Return the normalized download strategy name."""
    value = (strategy or get_env(DOWNLOAD_STRATEGY_ENV, "race")).strip().lower()
    value = value.replace("-", "_")
    if value in {"race", "oa_first", "sequential"}:
        return value
    return "race"


def _libgen_enabled(use_libgen: Optional[bool] = None) -> bool:
    if use_libgen is not None:
        return bool(use_libgen)
    return _env_flag_enabled(LIBGEN_ENABLED_ENV, default="false")


def _invalid_mcp_save_path(
    save_path: str,
    *,
    custom_save_path_confirmed: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return a structured error when an MCP save path override is not allowed.

    When *save_path* differs from the default, callers must pass
    ``custom_save_path_confirmed=True``.  This keeps an agent from inventing a
    custom output directory when the user did not explicitly ask for one.
    """
    requested = resolve_save_path(save_path)
    default = resolve_save_path(DEFAULT_SAVE_PATH)
    if requested == default:
        return None

    if not custom_save_path_confirmed:
        return {
            "status": "invalid_save_path",
            "message": (
                f"Custom MCP save_path overrides require explicit confirmation. Omit save_path to use "
                f"{DEFAULT_SAVE_PATH}, or pass custom_save_path_confirmed=true only when the user "
                "explicitly requested this directory."
            ),
            "requested_save_path": requested,
            "default_save_path": default,
            "confirm_param": "custom_save_path_confirmed",
            "require_env": f"PAPER_SEARCH_MCP_{REQUIRE_EXPLICIT_SAVE_PATH_ENV}",
        }

    if _custom_save_paths_allowed():
        return None

    return {
        "status": "invalid_save_path",
        "message": (
            f"MCP save_path overrides are disabled by PAPER_SEARCH_MCP_{ALLOW_CUSTOM_SAVE_PATH_ENV}=false. "
            f"Omit save_path to use {DEFAULT_SAVE_PATH}, remove the override, or set "
            f"PAPER_SEARCH_MCP_{ALLOW_CUSTOM_SAVE_PATH_ENV}=true to allow custom directories."
        ),
        "requested_save_path": requested,
        "default_save_path": default,
        "allow_env": f"PAPER_SEARCH_MCP_{ALLOW_CUSTOM_SAVE_PATH_ENV}",
    }


# ---------------------------------------------------------------------------
# PDF validation
# ---------------------------------------------------------------------------

def _is_valid_pdf_file(path: Any) -> bool:
    if not isinstance(path, (str, os.PathLike)):
        return False
    try:
        target = Path(path).expanduser()
        if not target.exists() or not target.is_file():
            return False
        with target.open("rb") as f:
            header = f.read(4096)
        return header.lstrip().startswith(b"%PDF")
    except OSError:
        return False


def _pdf_result_metadata(path: str) -> Dict[str, Any]:
    target = Path(path).expanduser().resolve()
    meta = {"pdf_path": str(target), "bytes": 0, "pdf_sha256": "", "valid_pdf": _is_valid_pdf_file(str(target))}
    try:
        meta["bytes"] = target.stat().st_size
        meta["pdf_sha256"] = sha256_file(target)
    except Exception:
        pass
    return meta


def cleanup_orphaned_temp_files(save_path: Optional[str] = None) -> Dict[str, Any]:
    """Remove orphaned .tmp files left behind by failed/interrupted downloads.

    Scans *save_path* (default ~/Desktop/papers) for any ``*.tmp`` files.
    These are always safe to delete because a live download atomically
    renames its temp file to the final name on success.
    """
    resolved = resolve_save_path(save_path or "")
    root = Path(resolved).expanduser()
    cleaned: List[str] = []
    bytes_freed = 0

    if not root.exists() or not root.is_dir():
        return {
            "save_path": str(root),
            "cleaned": 0,
            "bytes_freed": 0,
            "errors": 0,
        }

    for candidate in root.iterdir():
        if not candidate.is_file():
            continue
        # Match both "file.pdf.tmp" and ".file.pdf.a1b2c3.tmp"
        if not candidate.name.endswith(".tmp"):
            continue
        try:
            size = candidate.stat().st_size
            candidate.unlink()
            cleaned.append(str(candidate))
            bytes_freed += size
        except OSError:
            pass

    if cleaned:
        logging.getLogger(__name__).info(
            "Cleaned %d orphaned .tmp file(s) from %s (%d bytes freed)",
            len(cleaned),
            str(root),
            bytes_freed,
        )

    return {
        "save_path": str(root),
        "cleaned": len(cleaned),
        "bytes_freed": bytes_freed,
        "errors": 0,
    }


def _candidate_download_id(candidate: Dict[str, Any]) -> str:
    return (
        str(candidate.get("paper_id") or "").strip()
        or str(candidate.get("doi") or "").strip()
        or str(candidate.get("title") or "").strip()
    )


def _existing_pdf_candidates(
    candidate: Dict[str, Any], *, index: int, save_path: str
) -> List[tuple[str, Path]]:
    root = Path(resolve_save_path(save_path)).expanduser()
    paths: List[tuple[str, Path]] = []
    local_pdf = str(candidate.get("local_pdf_path") or "").strip()
    if local_pdf:
        paths.append(("local_pdf_path", Path(local_pdf).expanduser()))
    source = str(candidate.get("source") or "").strip().lower()
    paper_id = str(candidate.get("paper_id") or "").strip()
    download_id = _candidate_download_id(candidate)
    stem = str(candidate.get("canonical_pdf_stem") or "").strip()
    if stem:
        paths.append(("existing_canonical", root / _pdf_filename_from_hint(stem, default=f"paper_{index}")))
    if candidate.get("pdf_url"):
        paths.append(("existing_direct_pdf_url", root / _pdf_filename_from_hint(download_id or f"paper_{index}")))
    if paper_id:
        for variant in (paper_id, paper_id.replace("/", "_"), _safe_filename(paper_id)):
            if variant:
                paths.append(("existing_source_native", root / _pdf_filename_from_hint(variant)))
                if source:
                    paths.append(("existing_source_native", root / _pdf_filename_from_hint(f"{source}_{variant}")))
    seen: set[str] = set()
    unique: List[tuple[str, Path]] = []
    for m, p in paths:
        k = str(p)
        if k not in seen:
            seen.add(k)
            unique.append((m, p))
    return unique


def _find_existing_pdf(
    candidate: Dict[str, Any], *, index: int, save_path: str
) -> tuple[str, str]:
    for method, path in _existing_pdf_candidates(candidate, index=index, save_path=save_path):
        path_str = str(path)
        if path_str.endswith(".tmp"):
            continue
        if _is_valid_pdf_file(path_str):
            return str(path.expanduser().resolve()), method
    return "", ""


def _find_partial_tmp(output_path: Path) -> Optional[Path]:
    """Return an existing .tmp partial-download file matching *output_path*, or None.

    Temp files are named ``.<output_name>.<random-hex>.tmp``.
    Only the newest matching file is returned.
    """
    parent = output_path.parent
    prefix = f".{output_path.name}."
    best: Optional[Path] = None
    best_mtime = 0.0
    try:
        for candidate in parent.iterdir():
            if not candidate.is_file():
                continue
            if candidate.name.startswith(prefix) and candidate.name.endswith(".tmp"):
                try:
                    mtime = candidate.stat().st_mtime
                except OSError:
                    continue
                if best is None or mtime > best_mtime:
                    best = candidate
                    best_mtime = mtime
    except OSError:
        pass
    return best


# ---------------------------------------------------------------------------
# Direct URL download
# ---------------------------------------------------------------------------

async def _download_from_url(
    pdf_url: str,
    save_path: str,
    filename_hint: str = "paper",
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    if not pdf_url:
        return None
    save_path = resolve_save_path(save_path)
    os.makedirs(save_path, exist_ok=True)
    output_path = Path(save_path) / _pdf_filename_from_hint(filename_hint)
    max_retries = _env_int(DOWNLOAD_MAX_RETRIES_ENV, 3, minimum=0)
    retry_backoff = _env_float(DOWNLOAD_RETRY_BACKOFF_ENV, 1.0, minimum=0.1)

    # ── Resume support: look for an existing partial .tmp file ──────────
    resume_pos = 0
    temp_path = output_path.with_name(
        f".{output_path.name}.{secrets.token_hex(6)}.tmp"
    )
    existing_tmp = _find_partial_tmp(output_path)
    if existing_tmp is not None:
        try:
            existing_size = existing_tmp.stat().st_size
        except OSError:
            existing_size = 0
        if existing_size > 0:
            resume_pos = existing_size
            temp_path = existing_tmp  # reuse the partial file
            logger.info(
                "Resuming partial download %s from byte %d",
                existing_tmp.name, resume_pos,
            )

    async def _stream(client_ctx):
        nonlocal resume_pos, temp_path
        headers = {}
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"
        async with client_ctx.stream("GET", pdf_url, headers=headers) as resp:
            if resume_pos > 0 and resp.status_code not in (206, 200):
                # Server does not support Range — restart from scratch
                temp_path.unlink(missing_ok=True)
                resume_pos = 0
                temp_path = output_path.with_name(
                    f".{output_path.name}.{secrets.token_hex(6)}.tmp"
                )
                async with client_ctx.stream("GET", pdf_url) as resp2:
                    resp = resp2
                    return await _stream_inner(resp, 0, temp_path)
            return await _stream_inner(resp, resume_pos, temp_path)

    async def _stream_inner(resp, start_pos: int, tpath: Path):
        if resp.status_code >= 500 or resp.status_code in (429, 408):
            raise httpx.HTTPStatusError(
                f"Server error {resp.status_code}",
                request=resp.request, response=resp,
            )
        if resp.status_code >= 400 and resp.status_code != 206:
            return None
        ct = (resp.headers.get("content-type") or "").lower()
        first = b""
        total = start_pos
        mode = "ab" if start_pos > 0 else "wb"
        with tpath.open(mode) as f:
            async for chunk in resp.aiter_bytes(1024 * 256):
                if not chunk:
                    continue
                if not first:
                    first = chunk[:4096]
                total += len(chunk)
                f.write(chunk)
        if total <= 0:
            tpath.unlink(missing_ok=True)
            return None
        if not ("pdf" in ct or first.startswith(b"%PDF") or pdf_url.lower().endswith(".pdf")):
            tpath.unlink(missing_ok=True)
            return None
        tpath.replace(output_path)
        return str(output_path)

    for attempt in range(max_retries + 1):
        try:
            if client:
                return await _stream(client)
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=_env_float(DOWNLOAD_TIMEOUT_ENV, 30.0, minimum=1.0),
            ) as c:
                return await _stream(c)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            sc = e.response.status_code if isinstance(e, httpx.HTTPStatusError) and e.response else None
            if sc in {429, 502, 503, 504} or sc is None:
                if attempt < max_retries:
                    await asyncio.sleep(retry_backoff * (2 ** attempt))
                    # Keep temp_path for Range resume on retry
                    continue
            temp_path.unlink(missing_ok=True)
            return None
        except asyncio.CancelledError:
            # Keep temp_path for resume on next invocation
            raise
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            logger.warning("Direct URL download failed for %s: %s", pdf_url, e)
            return None
    temp_path.unlink(missing_ok=True)
    return None


# ---------------------------------------------------------------------------
# OA fallback chain
# ---------------------------------------------------------------------------

async def _try_repository_fallback(
    doi: str,
    title: str,
    save_path: str,
    *,
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[Optional[str], str]:
    if not repository_searchers:
        return None, "no repository searchers configured"
    queries = [q for q in [(doi or "").strip(), (title or "").strip()] if q]
    if not queries:
        return None, "no DOI/title for repository fallback"
    errors: List[str] = []
    for repo_name, searcher in repository_searchers:
        for q in queries:
            try:
                papers = await asyncio.to_thread(searcher.search, q, max_results=3)
            except Exception as e:
                errors.append(f"{repo_name}:{e}")
                continue
            for paper in (papers or []):
                if not _repository_paper_matches_request(paper, doi=doi, title=title):
                    continue
                pdf_url = str(getattr(paper, "pdf_url", "") or "").strip()
                if not pdf_url:
                    continue
                hint = _canonical_pdf_stem(
                    source=repo_name, paper_id=str(getattr(paper, "paper_id", q)),
                    doi=doi or _paper_value(getattr(paper, "doi", "")),
                    title=title or _paper_value(getattr(paper, "title", "")),
                    pdf_url=pdf_url, url=_paper_value(getattr(paper, "url", "")),
                    fallback=f"{repo_name}_{q}",
                )
                dl = await _download_from_url(pdf_url, save_path, hint, client=client)
                if dl:
                    return dl, ""
    return None, "; ".join(errors)


def _primary_downloaders(*, searchers: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dict mapping source name to its download_pdf callable."""
    source_names = [
        "arxiv", "biorxiv", "medrxiv", "iacr", "semantic",
        "pubmed", "crossref", "pmc", "core", "europepmc",
        "citeseerx", "doaj", "base", "zenodo", "hal", "ssrn",
    ]
    result: Dict[str, Any] = {}
    for name in source_names:
        s = searchers.get(name)
        if s is not None and hasattr(s, "download_pdf"):
            result[name] = s.download_pdf
    return result


def _coerce_searchers(searchers: Any, source: str = "") -> Dict[str, Any]:
    if isinstance(searchers, dict):
        return searchers
    if searchers is None:
        return {}
    key = (source or getattr(searchers, "source", "") or searchers.__class__.__name__).strip().lower()
    if key.endswith("searcher"):
        key = key[:-8]
    return {key or "source": searchers}


async def _try_primary_download(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    searchers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dl = _primary_downloaders(searchers=_coerce_searchers(searchers, source_name)).get(source_name)
    if not dl:
        return {"method": "primary", "path": None, "error": f"Unsupported source '{source_name}'."}
    try:
        result = await asyncio.to_thread(dl, paper_id, save_path)
    except Exception as e:
        return {"method": "primary", "path": None, "error": str(e)}
    if _looks_like_pdf_path(result) and os.path.exists(result):
        record_download(pdf_path=result, source=source_name, paper_id=paper_id, doi=doi, title=title,
                       downloader=f"{source_name}.download_pdf", legal_status="source_native_or_open_access")
        return {"method": "primary", "path": result, "downloader": f"{source_name}.download_pdf", "legal_status": "source_native_or_open_access"}
    return {"method": "primary", "path": None, "error": str(result or "no PDF returned")}


async def _try_repository_download(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    repository_searchers: Optional[List[tuple[str, Any]]] = None, client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    path, err = await _try_repository_fallback(doi, title, save_path, repository_searchers=repository_searchers, client=client)
    if path and os.path.exists(path):
        record_download(pdf_path=path, source=source_name, paper_id=paper_id, doi=doi, title=title,
                       downloader="repository_fallback", legal_status="open_access_repository")
        return {"method": "repositories", "path": path, "downloader": "repository_fallback", "legal_status": "open_access_repository"}
    return {"method": "repositories", "path": None, "error": err or "no repository PDF found"}


async def _try_unpaywall_download(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    unpaywall_resolver: Any = None, client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    ndoi = (doi or "").strip()
    if not ndoi:
        return {"method": "unpaywall", "path": None, "error": "DOI not provided"}
    if not unpaywall_resolver:
        return {"method": "unpaywall", "path": None, "error": "Unpaywall resolver not configured"}
    try:
        url = await asyncio.to_thread(unpaywall_resolver.resolve_best_pdf_url, ndoi)
    except Exception as e:
        return {"method": "unpaywall", "path": None, "error": str(e)}
    if not url:
        return {"method": "unpaywall", "path": None, "error": "no OA URL found"}
    dl = await _download_from_url(url, save_path, f"unpaywall_{ndoi}", client=client)
    if dl and os.path.exists(dl):
        record_download(pdf_path=dl, source=source_name, paper_id=paper_id, doi=doi, title=title,
                       downloader="unpaywall", legal_status="open_access_unpaywall")
        return {"method": "unpaywall", "path": dl, "downloader": "unpaywall", "legal_status": "open_access_unpaywall"}
    return {"method": "unpaywall", "path": None, "error": "resolved OA URL but download failed"}


async def _try_publisher_direct_download(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    from ..academic_platforms.publisher_direct import resolve_publisher_direct_url

    direct_url = resolve_publisher_direct_url(doi)
    if not direct_url:
        return {"method": "publisher_direct", "path": None, "error": "no known OA publisher direct URL"}
    hint = _canonical_pdf_stem(
        source="publisher_direct",
        paper_id=paper_id,
        doi=doi,
        title=title,
        pdf_url=direct_url,
        fallback=f"publisher_direct_{doi or paper_id or title}",
    )
    dl = await _download_from_url(direct_url, save_path, hint, client=client)
    if dl and os.path.exists(dl):
        record_download(
            pdf_path=dl,
            source=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="publisher_direct",
            legal_status="open_access_publisher_direct",
        )
        return {
            "method": "publisher_direct",
            "path": dl,
            "downloader": "publisher_direct",
            "legal_status": "open_access_publisher_direct",
        }
    return {"method": "publisher_direct", "path": None, "error": "direct OA URL failed PDF validation or download"}


async def _try_libgen_download(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    libgen_base_url: str = "",
) -> Dict[str, Any]:
    ident = (doi or "").strip() or (title or "").strip() or (paper_id or "").strip()
    if not ident:
        return {"method": "libgen", "path": None, "error": "no DOI, title, or paper_id provided"}
    try:
        from ..academic_platforms.libgen import LibGenFetcher
    except Exception as exc:
        return {"method": "libgen", "path": None, "error": f"LibGen fetcher import failed: {exc}"}

    try:
        fetcher = LibGenFetcher(
            base_url=libgen_base_url or get_env(LIBGEN_BASE_URL_ENV, ""),
            output_dir=save_path,
            timeout=_env_float(DOWNLOAD_TIMEOUT_ENV, 30.0, minimum=1.0),
        )
        path = await asyncio.to_thread(fetcher.download_pdf, ident)
    except Exception as exc:
        return {"method": "libgen", "path": None, "error": str(exc)}
    if path and os.path.exists(path) and _is_valid_pdf_file(path):
        record_download(
            pdf_path=path,
            source=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="libgen",
            legal_status="user_opt_in_libgen",
        )
        return {
            "method": "libgen",
            "path": path,
            "downloader": "libgen",
            "legal_status": "user_opt_in_libgen",
        }
    return {"method": "libgen", "path": None, "error": "LibGen did not return a valid PDF"}


def _paper_fetch_pdf_query(*, paper_id: str, doi: str, title: str) -> str:
    return (doi or "").strip() or (paper_id or "").strip() or (title or "").strip()


def _paper_fetch_saved_pdf_path(before: Dict[str, tuple[int, int]], save_path: str) -> str:
    for pdf_path in reversed(_changed_pdf_paths(before, save_path)):
        if _is_valid_pdf_file(pdf_path):
            return pdf_path
    return ""


def _run_paper_fetch_pdf(query: str, save_path: str) -> tuple[str, str]:
    try:
        from paper_fetch import FetchStrategy, fetch_paper
        from paper_fetch.runtime import RuntimeContext
    except Exception as exc:
        return "", f"paper_fetch import failed: {exc}"

    before = _snapshot_pdf_files(save_path)
    context = RuntimeContext(download_dir=Path(save_path), artifact_mode="all")
    try:
        fetch_paper(
            query,
            modes={"metadata"},
            strategy=FetchStrategy(asset_profile="none"),
            context=context,
        )
    except Exception as exc:
        return "", str(exc)
    finally:
        close = getattr(context, "close", None)
        if callable(close):
            with suppress(Exception):
                close()

    pdf_path = _paper_fetch_saved_pdf_path(before, save_path)
    if pdf_path:
        return pdf_path, ""
    return "", "paper_fetch completed without saving a valid PDF"


async def _try_paper_fetch_download(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
) -> Dict[str, Any]:
    if not _env_flag_enabled(PAPER_FETCH_FALLBACK_ENV, default="false"):
        return {
            "method": "paper_fetch",
            "path": None,
            "error": f"disabled by PAPER_SEARCH_MCP_{PAPER_FETCH_FALLBACK_ENV}",
        }
    query = _paper_fetch_pdf_query(paper_id=paper_id, doi=doi, title=title)
    if not query:
        return {"method": "paper_fetch", "path": None, "error": "no DOI, paper_id, or title provided"}
    started = time.perf_counter()
    path, error = await asyncio.to_thread(_run_paper_fetch_pdf, query, save_path)
    elapsed = time.perf_counter() - started
    ok = bool(path and _is_valid_pdf_file(path))
    await asyncio.to_thread(
        record_download_health,
        method="paper_fetch",
        source=source_name,
        ok=ok,
        elapsed_seconds=elapsed,
        error=error,
    )
    if ok:
        await asyncio.to_thread(
            record_download,
            pdf_path=path,
            source=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="paper_fetch",
            legal_status="paper_fetch_provider_open_access_or_entitled",
        )
        return {
            "method": "paper_fetch",
            "path": path,
            "downloader": "paper_fetch",
            "legal_status": "paper_fetch_provider_open_access_or_entitled",
            "elapsed_seconds": round(elapsed, 3),
        }
    return {
        "method": "paper_fetch",
        "path": None,
        "error": error or "paper_fetch did not return a valid PDF",
        "elapsed_seconds": round(elapsed, 3),
    }


async def _attempt_download_method(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    method: str,
    searchers: Dict[str, Any],
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    client: Optional[httpx.AsyncClient] = None,
    libgen_base_url: str = "",
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        if method == "publisher_direct":
            result = await _try_publisher_direct_download(
                source_name=source_name, paper_id=paper_id, doi=doi, title=title,
                save_path=save_path, client=client,
            )
        elif method == "primary":
            result = await _try_primary_download(
                source_name=source_name, paper_id=paper_id, doi=doi, title=title,
                save_path=save_path, searchers=searchers,
            )
        elif method == "repositories":
            result = await _try_repository_download(
                source_name=source_name, paper_id=paper_id, doi=doi, title=title,
                save_path=save_path, repository_searchers=repository_searchers,
                client=client,
            )
        elif method == "unpaywall":
            result = await _try_unpaywall_download(
                source_name=source_name, paper_id=paper_id, doi=doi, title=title,
                save_path=save_path, unpaywall_resolver=unpaywall_resolver,
                client=client,
            )
        elif method == "paper_fetch":
            result = await _try_paper_fetch_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
            )
        elif method == "libgen":
            result = await _try_libgen_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                libgen_base_url=libgen_base_url,
            )
        else:
            result = {"method": method, "path": None, "error": f"Unknown download method '{method}'"}
        ok = bool(result.get("path") and _is_valid_pdf_file(result.get("path")))
        elapsed = time.perf_counter() - started
        if method != "paper_fetch":
            await asyncio.to_thread(
                record_download_health,
                method=method,
                source=source_name,
                ok=ok,
                elapsed_seconds=elapsed,
                error=str(result.get("error") or ""),
            )
        result["elapsed_seconds"] = round(elapsed, 3)
        return result
    except Exception as exc:
        elapsed = time.perf_counter() - started
        await asyncio.to_thread(
            record_download_health,
            method=method,
            source=source_name,
            ok=False,
            elapsed_seconds=elapsed,
            error=str(exc),
        )
        raise


async def _race_download_methods(
    methods: List[str],
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    searchers: Dict[str, Any],
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    client: Optional[httpx.AsyncClient] = None,
    libgen_base_url: str = "",
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    if not methods:
        return None, []

    tasks = {
        asyncio.create_task(
            _attempt_download_method(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                method=method,
                searchers=searchers,
                repository_searchers=repository_searchers,
                unpaywall_resolver=unpaywall_resolver,
                client=client,
                libgen_base_url=libgen_base_url,
            )
        ): method
        for method in methods
    }
    errors: List[str] = []
    try:
        for completed in asyncio.as_completed(tasks):
            try:
                r = await completed
            except Exception as e:
                errors.append(str(e))
                continue
            if _looks_like_pdf_path(r.get("path")):
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return r, errors
            if r.get("error"):
                errors.append(f"{r.get('method', 'download')}: {r['error']}")
    finally:
        pending = [t for t in tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return None, errors


async def _sequential_download_methods(
    methods: List[str],
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    searchers: Dict[str, Any],
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    client: Optional[httpx.AsyncClient] = None,
    libgen_base_url: str = "",
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    for method in methods:
        try:
            result = await _attempt_download_method(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                method=method,
                searchers=searchers,
                repository_searchers=repository_searchers,
                unpaywall_resolver=unpaywall_resolver,
                client=client,
                libgen_base_url=libgen_base_url,
            )
        except Exception as exc:
            errors.append(f"{method}: {exc}")
            continue
        if result.get("path") and _is_valid_pdf_file(result.get("path")):
            return result, errors
        if result.get("error"):
            errors.append(f"{result.get('method', method)}: {result['error']}")
    return None, errors


async def _race_oa_downloads(
    *, source_name: str, paper_id: str, doi: str, title: str, save_path: str,
    searchers: Dict[str, Any],
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    client: Optional[httpx.AsyncClient] = None,
    strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    resolved_strategy = _download_strategy(strategy)
    ranked_oa_methods = rank_download_methods(["primary", "repositories", "unpaywall"], source=source_name)
    if doi and "publisher_direct" not in ranked_oa_methods:
        ranked_oa_methods = ["publisher_direct", *ranked_oa_methods]
    libgen_allowed = _libgen_enabled(use_libgen)

    if resolved_strategy == "sequential":
        methods = [*ranked_oa_methods, "paper_fetch"]
        if libgen_allowed:
            methods.append("libgen")
        return await _sequential_download_methods(
            methods,
            source_name=source_name,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            searchers=searchers,
            repository_searchers=repository_searchers,
            unpaywall_resolver=unpaywall_resolver,
            client=client,
            libgen_base_url=libgen_base_url,
        )

    race_methods = list(ranked_oa_methods)
    if resolved_strategy == "race" and libgen_allowed:
        race_methods.append("libgen")

    result, errors = await _race_download_methods(
        race_methods,
        source_name=source_name,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
        searchers=searchers,
        repository_searchers=repository_searchers,
        unpaywall_resolver=unpaywall_resolver,
        client=client,
        libgen_base_url=libgen_base_url,
    )
    if result:
        return result, errors
    if resolved_strategy == "oa_first":
        return None, errors
    return None, errors


# ---------------------------------------------------------------------------
# Full fallback download
# ---------------------------------------------------------------------------

async def _download_with_fallback_path(
    source: str, paper_id: str, doi: str = "", title: str = "",
    save_path: str = DEFAULT_SAVE_PATH, use_scihub: bool = False,
    scihub_base_url: str = "https://sci-hub.se",
    download_strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
    searchers: Optional[Dict[str, Any]] = None,
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Attempt to download a paper PDF through the OA-fallback chain.

    Returns a structured dict with keys:
    * On success: ``{"status": "ok", "pdf_path": str, "download_method": str}``
    * On failure: ``{"status": "download_failed", "message": str,
      "tried_methods": [...], "available_options": [...]}``
    """
    save_path = resolve_save_path(save_path)
    sn = source.strip().lower()
    sn, pid, pdoi = _source_from_identifier(sn, paper_id, doi)
    paper_id, doi = pid, pdoi
    arxiv_id = _extract_arxiv_id(paper_id, doi, title)
    if arxiv_id:
        sn, paper_id = "arxiv", arxiv_id
    tried_methods: List[str] = []
    result, errors = await _race_oa_downloads(
        source_name=sn, paper_id=paper_id, doi=doi, title=title, save_path=save_path,
        searchers=searchers or {}, repository_searchers=repository_searchers,
        unpaywall_resolver=unpaywall_resolver, client=client,
        strategy=download_strategy, use_libgen=use_libgen, libgen_base_url=libgen_base_url,
    )
    tried_methods.append("oa_race")
    if result and isinstance(result.get("path"), str):
        return {"status": "ok", "pdf_path": result["path"],
                "download_method": result.get("method", "oa_race")}
    strategy_name = _download_strategy(download_strategy)
    if strategy_name != "sequential":
        tried_methods.append("paper_fetch")
        paper_fetch_result = await _try_paper_fetch_download(
            source_name=sn,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
        )
        if isinstance(paper_fetch_result.get("path"), str):
            return {"status": "ok", "pdf_path": paper_fetch_result["path"],
                    "download_method": "paper_fetch"}
        if paper_fetch_result.get("error"):
            errors.append(f"paper_fetch: {paper_fetch_result['error']}")
        if _libgen_enabled(use_libgen) and strategy_name == "oa_first":
            tried_methods.append("libgen")
            libgen_result = await _try_libgen_download(
                source_name=sn,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
                libgen_base_url=libgen_base_url,
            )
            if isinstance(libgen_result.get("path"), str):
                return {"status": "ok", "pdf_path": libgen_result["path"],
                        "download_method": "libgen"}
            if libgen_result.get("error"):
                errors.append(f"libgen: {libgen_result['error']}")

    # ── Build available-options guidance ─────────────────────────────
    available_options: List[Dict[str, Any]] = []
    if doi:
        available_options.append({
            "method": "publisher_download",
            "description": "Try publisher version via scansci-pdf (may need setup)",
            "tool": "download_publisher_version",
        })
    if not use_scihub:
        available_options.append({
            "method": "scihub",
            "description": "Opt-in Sci-Hub fallback",
            "tool": "download_with_fallback",
            "param": "use_scihub=true",
        })
    if not _libgen_enabled(use_libgen):
        available_options.append({
            "method": "libgen",
            "description": "Opt-in LibGen fallback",
            "env": "PAPER_SEARCH_MCP_LIBGEN_ENABLED=true",
        })

    if not use_scihub:
        return {
            "status": "download_failed",
            "message": "Download failed after OA fallback chain.",
            "tried_methods": tried_methods,
            "errors": errors,
            "doi": doi or "",
            "available_options": available_options,
        }

    tried_methods.append("scihub")
    from ..academic_platforms.sci_hub import SciHubFetcher
    ident = (doi or "").strip() or (title or "").strip() or paper_id
    fetcher = SciHubFetcher(base_url=scihub_base_url, output_dir=save_path)
    fb = await asyncio.to_thread(fetcher.download_pdf, ident)
    if fb and os.path.exists(fb):
        record_download(pdf_path=fb, source=sn, paper_id=paper_id, doi=doi, title=title,
                       downloader="scihub", legal_status="user_opt_in_scihub")
        return {"status": "ok", "pdf_path": str(fb), "download_method": "scihub"}
    return {
        "status": "download_failed",
        "message": "All download methods failed, including Sci-Hub.",
        "tried_methods": tried_methods,
        "errors": errors,
        "doi": doi or "",
        "available_options": available_options,
    }


# ---------------------------------------------------------------------------
# Source-native download & read
# ---------------------------------------------------------------------------

async def _download_source_pdf(
    searchers: Dict[str, Any],
    *,
    source: str,
    paper_id: str,
    save_path: str,
    ctx: Any = None,
    doi: str = "",
    title: str = "",
    downloader: str = "",
    legal_status: str = "source_native_or_open_access",
    parse_execution: str = "background",
    custom_save_path_confirmed: bool = False,
    after_save_hook: Any = None,
) -> Any:
    """Download a paper PDF from the given source using its registered searcher.

    Args:
        searchers: Dict mapping source name (e.g. 'arxiv') to searcher instance.
        source: The academic source name.
        paper_id: Source-native paper identifier.
        save_path: Directory to save the downloaded PDF.
        ctx: Optional context (kept for signature compatibility).
        doi: Optional DOI for fallback routing.
        title: Optional title for fallback routing.
        downloader: Optional downloader label override.
        legal_status: Legal status label for the download.
        parse_execution: 'background', 'sync', 'prompt', or 'none'.
        custom_save_path_confirmed: Whether a custom save_path was explicitly confirmed.
        after_save_hook: Optional async callable hook for post-download processing.
    """
    invalid = _invalid_mcp_save_path(save_path, custom_save_path_confirmed=custom_save_path_confirmed)
    if invalid:
        return invalid
    save_path = resolve_save_path(save_path)
    rsrc, rpid, rdoi = _source_from_identifier(source, paper_id, doi)
    if rsrc != source:
        source, paper_id, doi = rsrc, rpid, rdoi
    searchers_dict = _coerce_searchers(searchers, source)
    searcher = _searcher_for_source(source, searchers=searchers_dict)
    if searcher is None:
        for fallback_name in [source, rsrc, "arxiv"]:
            s = searchers_dict.get(fallback_name)
            if s is not None:
                searcher = s
                source = fallback_name
                break
    candidate = _paper_parse_candidate({"source": source, "paper_id": paper_id, "doi": doi, "title": title}, 1)
    epath, emethod = _find_existing_pdf(candidate, index=1, save_path=save_path)
    if epath:
        if after_save_hook:
            resp = await after_save_hook(
                {"pdf_path": epath, "pdf_paths": [epath]},
                source=source, paper_id=paper_id, doi=doi, title=title, save_path=save_path,
                downloader=downloader or f"{source}.download_pdf", legal_status=legal_status,
                ctx=ctx, parse_execution=parse_execution, custom_save_path_confirmed=custom_save_path_confirmed,
            )
            if isinstance(resp, dict):
                resp["status"] = "skipped_existing"
                resp["download_method"] = emethod
                resp.update(_pdf_result_metadata(epath))
            return resp
        return {
            "status": "skipped_existing",
            "pdf_path": epath,
            "source": source,
            "paper_id": paper_id,
            "doi": doi,
            "title": title or Path(epath).stem,
            "download_method": emethod,
            **_pdf_result_metadata(epath),
        }
    if searcher is None:
        return f"No searcher available for source '{source}' and paper_id '{paper_id}'"
    try:
        kwargs: Dict[str, Any] = {}
        if source == "arxiv":
            kwargs["timeout"] = _env_float(DOWNLOAD_TIMEOUT_ENV, 20.0, minimum=1.0)
        result = await asyncio.to_thread(searcher.download_pdf, paper_id, save_path, **kwargs)
    except NotImplementedError as e:
        return str(e)
    if after_save_hook:
        return await after_save_hook(
            result, source=source, paper_id=paper_id, doi=doi, title=title, save_path=save_path,
            downloader=downloader or f"{source}.download_pdf", legal_status=legal_status,
            ctx=ctx, parse_execution=parse_execution, custom_save_path_confirmed=custom_save_path_confirmed,
        )
    return result


async def _read_source_paper(
    searchers: Dict[str, Any],
    *,
    source: str,
    paper_id: str,
    save_path: str,
    ctx: Any = None,
    doi: str = "",
    title: str = "",
    custom_save_path_confirmed: bool = False,
    after_save_hook: Any = None,
) -> Any:
    """Read/extract text from a paper by downloading it first if needed.

    Args:
        searchers: Dict mapping source name to searcher instance.
        source: The academic source name.
        paper_id: Source-native paper identifier.
        save_path: Directory for downloaded PDFs.
        ctx: Optional context (kept for signature compatibility).
        doi: Optional DOI.
        title: Optional title.
        custom_save_path_confirmed: Whether a custom save_path was explicitly confirmed.
        after_save_hook: Optional async callable for post-download processing.
    """
    invalid = _invalid_mcp_save_path(save_path, custom_save_path_confirmed=custom_save_path_confirmed)
    if invalid:
        return invalid
    rsp = resolve_save_path(save_path)
    searchers_dict = _coerce_searchers(searchers, source)
    searcher = _searcher_for_source(source, searchers=searchers_dict)
    if searcher is None:
        for fallback_name in [source, "arxiv"]:
            s = searchers_dict.get(fallback_name)
            if s is not None:
                searcher = s
                break
    if searcher is None:
        return f"No searcher available for source '{source}'"
    before = _snapshot_pdf_files(rsp)
    try:
        result = await asyncio.to_thread(searcher.read_paper, paper_id, rsp)
    except Exception as e:
        logger.warning("Read failed for %s/%s: %s", source, paper_id, e)
        return ""
    changed_pdfs = _changed_pdf_paths(before, rsp)
    parse_prompt = None
    if after_save_hook and changed_pdfs:
        normalized: List[str] = []
        for pdf_path in changed_pdfs:
            resolved = _pdf_path_from_result(pdf_path)
            if resolved and resolved not in normalized:
                normalized.append(resolved)
        if normalized:
            parse_prompt = await after_save_hook(
                {"pdf_paths": normalized},
                source=source, paper_id=paper_id, save_path=rsp,
                downloader=f"{source}.read_paper", doi=doi, title=title,
                ctx=ctx,
            )
    if parse_prompt is None:
        return result
    return {"status": "read", "source": source, "paper_id": paper_id, "doi": doi,
            "title": title, "text": result, "saved_pdf_prompt": parse_prompt}


# ---------------------------------------------------------------------------
# Session paper resolution
# ---------------------------------------------------------------------------

async def _resolve_session_paper_pdf(
    *, paper: Dict[str, Any], index: int, save_path: str, use_scihub: bool,
    searchers: Optional[Dict[str, Any]] = None,
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    download_strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
) -> Dict[str, Any]:
    candidate = _paper_parse_candidate(paper, index)
    if not candidate["parse_ready"]:
        return {"index": index, "status": "skipped", "candidate": candidate, "message": candidate["reason"]}
    s, pid, doi, title = candidate["source"], candidate["paper_id"], candidate["doi"], candidate["title"]
    pdf_url, local = candidate["pdf_url"], candidate.get("local_pdf_path", "")
    dl_id = pid or doi or title
    if local and os.path.exists(local):
        return {"index": index, "status": "ready", "candidate": candidate, "download_method": "local_pdf_path", "pdf_path": local}
    if pdf_url:
        hint = str(candidate.get("canonical_pdf_stem") or dl_id or f"paper_{index}")
        dp = await _download_from_url(pdf_url, save_path, hint)
        if dp and os.path.exists(dp):
            record_download(pdf_path=dp, source=s, paper_id=pid, doi=doi, title=title,
                           downloader="search_result_pdf_url", legal_status="search_result_open_access_pdf_url")
            return {"index": index, "status": "ready", "candidate": candidate, "download_method": "search_result_pdf_url", "pdf_path": dp}
    fb_result = await _download_with_fallback_path(source=s, paper_id=dl_id, doi=doi, title=title, save_path=save_path,
                                            use_scihub=use_scihub, searchers=searchers,
                                            repository_searchers=repository_searchers, unpaywall_resolver=unpaywall_resolver,
                                            download_strategy=download_strategy, use_libgen=use_libgen,
                                            libgen_base_url=libgen_base_url)
    if fb_result.get("status") != "ok":
        return {"index": index, "status": "download_failed", "candidate": candidate,
                "message": fb_result.get("message", "Download failed"),
                "tried_methods": fb_result.get("tried_methods", []),
                "available_options": fb_result.get("available_options", [])}
    fp = fb_result["pdf_path"]
    if not os.path.exists(fp):
        return {"index": index, "status": "download_failed", "candidate": candidate,
                "message": f"Download reported ok but file not found: {fp}"}
    return {"index": index, "status": "ready", "candidate": candidate,
            "download_method": fb_result.get("download_method", "download_with_fallback"), "pdf_path": fp}


async def _download_selected_session_paper(
    *, paper: Dict[str, Any], index: int, save_path: str, use_scihub: bool,
    searchers: Optional[Dict[str, Any]] = None,
    repository_searchers: Optional[List[tuple[str, Any]]] = None,
    unpaywall_resolver: Any = None,
    client: Optional[httpx.AsyncClient] = None,
    download_strategy: str = "",
    use_libgen: Optional[bool] = None,
    libgen_base_url: str = "",
) -> Dict[str, Any]:
    candidate = _paper_parse_candidate(paper, index)
    if not candidate["parse_ready"]:
        return {"index": index, "status": "skipped", "candidate": candidate, "message": candidate["reason"]}
    epath, emethod = _find_existing_pdf(candidate, index=index, save_path=save_path)
    if epath:
        return {"index": index, "status": "skipped_existing", "candidate": candidate, "download_method": emethod, **_pdf_result_metadata(epath)}
    s, pid, doi, title = candidate["source"], candidate["paper_id"], candidate["doi"], candidate["title"]
    pdf_url = candidate["pdf_url"]
    dl_id = _candidate_download_id(candidate)
    # Validate local path
    local = str(candidate.get("local_pdf_path") or "").strip()
    if local and Path(local).expanduser().exists() and not _is_valid_pdf_file(local):
        if not (pdf_url or pid or doi or title):
            return {"index": index, "status": "invalid_pdf", "candidate": candidate, "pdf_path": local, "message": "Existing local_pdf_path is not a valid PDF."}
    if pdf_url:
        hint = str(candidate.get("canonical_pdf_stem") or dl_id or f"paper_{index}")
        dp = await _download_from_url(pdf_url, save_path, hint, client=client)
        if dp and os.path.exists(dp):
            if not _is_valid_pdf_file(dp):
                return {"index": index, "status": "invalid_pdf", "candidate": candidate, "download_method": "search_result_pdf_url", "pdf_path": dp, "message": "Downloaded file failed PDF validation."}
            await asyncio.to_thread(record_download, pdf_path=dp, source=s, paper_id=pid, doi=doi, title=title,
                                   downloader="search_result_pdf_url", legal_status="search_result_open_access_pdf_url")
            return {"index": index, "status": "downloaded", "candidate": candidate, "download_method": "search_result_pdf_url", **_pdf_result_metadata(dp)}
    fb_result = await _download_with_fallback_path(source=s, paper_id=dl_id, doi=doi, title=title, save_path=save_path,
                                            use_scihub=use_scihub, searchers=searchers,
                                            repository_searchers=repository_searchers, unpaywall_resolver=unpaywall_resolver,
                                            client=client, download_strategy=download_strategy,
                                            use_libgen=use_libgen, libgen_base_url=libgen_base_url)
    if fb_result.get("status") != "ok":
        return {"index": index, "status": "download_failed", "candidate": candidate,
                "message": fb_result.get("message", "Download failed"),
                "tried_methods": fb_result.get("tried_methods", []),
                "available_options": fb_result.get("available_options", [])}
    fp = fb_result["pdf_path"]
    if not os.path.exists(fp):
        return {"index": index, "status": "download_failed", "candidate": candidate,
                "message": f"Download reported ok but file not found: {fp}"}
    if not _is_valid_pdf_file(fp):
        return {"index": index, "status": "invalid_pdf", "candidate": candidate, "download_method": "download_with_fallback", "pdf_path": fp, "message": "Downloaded file failed PDF validation."}
    return {"index": index, "status": "downloaded", "candidate": candidate, "download_method": "download_with_fallback", **_pdf_result_metadata(fp)}


def _download_manifest_path(save_path: str, selection_token: str) -> str:
    root = Path(resolve_save_path(save_path))
    root.mkdir(parents=True, exist_ok=True)
    return str((root / f"paper_search_download_manifest_{_safe_filename(selection_token, 'selection')}.json").resolve())


# ---------------------------------------------------------------------------
# PDF path helpers
# ---------------------------------------------------------------------------

def _pdf_path_from_result(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    path = Path(value).expanduser()
    if path.exists() and path.is_file() and path.suffix.lower() == ".pdf":
        return str(path.resolve())
    return ""


def _pdf_paths_from_result(value: Any) -> List[str]:
    if isinstance(value, dict):
        raw_paths: List[Any] = []
        for key in ("pdf_path", "local_pdf_path"):
            raw_paths.append(value.get(key))
        raw_paths.extend(value.get("pdf_paths") or [])
        paths: List[str] = []
        for item in raw_paths:
            path = _pdf_path_from_result(item)
            if path and path not in paths:
                paths.append(path)
        return paths
    path = _pdf_path_from_result(value)
    return [path] if path else []


# ---------------------------------------------------------------------------
# Download metadata builders
# ---------------------------------------------------------------------------

def _downloaded_pdf_paper(
    *,
    pdf_path: str,
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    authors: str = "",
    year: str = "",
    published_date: str = "",
    publication_venue: str = "",
    pdf_url: str = "",
    url: str = "",
) -> Dict[str, Any]:
    path = Path(pdf_path).expanduser().resolve()
    return {
        "title": title or path.stem,
        "authors": authors,
        "year": year,
        "published_date": published_date,
        "publication_venue": publication_venue,
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "pdf_url": pdf_url,
        "local_pdf_path": str(path),
        "url": url,
    }


def _paper_from_download_metadata(
    pdf_path: str,
    *,
    searchers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a paper dict from download-cache metadata, enriched with arXiv when available."""
    path = Path(pdf_path).expanduser().resolve()
    metadata = find_download_by_pdf_path(str(path))
    if not isinstance(metadata, dict):
        metadata = {}
    source = _paper_field(metadata, "source") or "local"
    paper_id = _paper_field(metadata, "paper_id") or path.stem
    doi = _paper_doi(metadata)
    title = _paper_field(metadata, "title")
    arxiv_id = _extract_arxiv_id(paper_id, doi, path.stem)
    if arxiv_id and (not title or title == path.stem or title == arxiv_id):
        if searchers and "arxiv" in searchers:
            try:
                arxiv_searcher = searchers["arxiv"]
                arxiv_paper = arxiv_searcher.get_by_id(
                    arxiv_id,
                    timeout=_env_float(SEARCH_SOURCE_TIMEOUT_ENV, 8.0, minimum=1.0),
                    max_attempts=_env_int("ARXIV_MAX_ATTEMPTS", 2, minimum=1),
                )
                if arxiv_paper:
                    arxiv_data = arxiv_paper.to_dict() if hasattr(arxiv_paper, "to_dict") else dict(arxiv_paper)
                    found_id = _extract_arxiv_id(
                        arxiv_data.get("paper_id"),
                        arxiv_data.get("doi"),
                        arxiv_data.get("pdf_url"),
                        arxiv_data.get("url"),
                    )
                    arxiv_metadata = {**arxiv_data, "source": "arxiv", "paper_id": found_id or arxiv_id}
                    metadata = {**metadata, **{key: value for key, value in arxiv_metadata.items() if value}}
                    source = _paper_field(metadata, "source") or "arxiv"
                    paper_id = _paper_field(metadata, "paper_id") or arxiv_id
                    doi = _paper_doi(metadata)
                    title = _paper_field(metadata, "title")
            except Exception:
                logger.debug("arXiv metadata lookup failed for %s", arxiv_id, exc_info=True)
    return _downloaded_pdf_paper(
        pdf_path=str(path),
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title or path.stem,
        authors=_paper_field(metadata, "authors"),
        year=_paper_year(metadata),
        published_date=_paper_publication_date(metadata),
        publication_venue=_paper_publication_venue(metadata),
        pdf_url=_paper_field(metadata, "pdf_url"),
        url=_paper_field(metadata, "url"),
    )


def _downloaded_pdf_papers(
    pdf_paths: List[str],
    *,
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    searchers: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    papers: List[Dict[str, Any]] = []
    for index, pdf_path in enumerate(pdf_paths):
        item_title = title
        if len(pdf_paths) > 1:
            item_title = f"{title or Path(pdf_path).stem} ({index + 1})"
        papers.append(_paper_from_download_metadata(pdf_path=pdf_path, searchers=searchers))
        if item_title:
            papers[-1]["title"] = item_title
        if source:
            papers[-1]["source"] = source
        if paper_id:
            papers[-1]["paper_id"] = paper_id
        if doi:
            papers[-1]["doi"] = doi
    return papers


# ---------------------------------------------------------------------------
# PDF tracking helpers
# ---------------------------------------------------------------------------

def _saved_pdf_batch_window_seconds() -> float:
    return _env_float(SAVED_PDF_BATCH_WINDOW_ENV, 600.0, minimum=0.0)


def _saved_pdf_batch_prompt_enabled() -> bool:
    return _env_flag_enabled(SAVED_PDF_BATCH_PROMPT_ENV, default="true")


def _snapshot_pdf_files(save_path: str) -> Dict[str, tuple[int, int]]:
    root = Path(resolve_save_path(save_path))
    if not root.exists() or not root.is_dir():
        return {}
    snapshot: Dict[str, tuple[int, int]] = {}
    for path in root.rglob("*.pdf"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[str(path.resolve())] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _changed_pdf_paths(before: Dict[str, tuple[int, int]], save_path: str) -> List[str]:
    after = _snapshot_pdf_files(save_path)
    changed: List[str] = []
    for path, signature in after.items():
        if before.get(path) != signature:
            changed.append(path)
    return sorted(changed)


def _recent_saved_pdf_papers(
    save_path: str,
    *,
    window_seconds: float = 0.0,
    searchers: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    root = Path(resolve_save_path(save_path)).expanduser()
    if not root.exists() or not root.is_dir():
        return []
    cutoff = time.time() - window_seconds if window_seconds and window_seconds > 0 else 0.0
    candidates: List[Path] = []
    for path in root.glob("*.pdf"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if cutoff and stat.st_mtime < cutoff:
            continue
        if not _is_valid_pdf_file(str(path)):
            continue
        candidates.append(path)
    candidates.sort(key=lambda item: item.stat().st_mtime)
    papers: List[Dict[str, Any]] = []
    for path in candidates:
        papers.append(_paper_from_download_metadata(str(path.resolve()), searchers=searchers))
    return papers


# ---------------------------------------------------------------------------
# Post-download processing
# ---------------------------------------------------------------------------

async def _after_saved_pdf(
    result: Any,
    *,
    source: str,
    paper_id: str,
    save_path: str,
    downloader: str,
    doi: str = "",
    title: str = "",
    legal_status: str = "source_native_or_open_access",
    ctx: Any = None,
    parse_execution: str = "background",
    custom_save_path_confirmed: bool = False,
    after_save_prompt_hook: Any = None,
    searchers: Optional[Dict[str, Any]] = None,
    _attach_local_selection_ui_fn: Optional[Any] = None,
) -> Any:
    """Record a downloaded PDF and optionally trigger a parse prompt.

    Args:
        result: Download result (str path, dict with pdf_path/pdf_paths, etc.).
        source: Academic source name.
        paper_id: Source-native paper identifier.
        save_path: Directory where the PDF was saved.
        downloader: Label for the download method.
        doi: Optional DOI.
        title: Optional title.
        legal_status: Legal status label.
        ctx: Optional context for prompt hooks.
        parse_execution: 'background', 'sync', 'prompt', or 'none'.
        custom_save_path_confirmed: Whether a custom save_path was confirmed.
        after_save_prompt_hook: Optional async callable for the parse prompt.
        searchers: Optional searchers dict for metadata enrichment.
        _attach_local_selection_ui_fn: Optional callable for local-browser UI attachment
            (used by orchestration layer callers).
    """
    pdf_paths = _pdf_paths_from_result(result)
    if not pdf_paths:
        return result

    for pdf_path in pdf_paths:
        record_download(
            pdf_path=pdf_path,
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title or Path(pdf_path).stem,
            downloader=downloader,
            legal_status=legal_status,
        )

    papers = _downloaded_pdf_papers(
        pdf_paths,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        searchers=searchers,
    )
    parse_execution_name = _workflow_parse_execution_name(parse_execution)
    prompt_papers = papers
    prompt_query = title or paper_id or Path(pdf_paths[0]).stem
    prompt_sources = source
    prompt_parse_execution = parse_execution
    batch_prompt_metadata: Dict[str, Any] = {}

    if (
        len(papers) <= AUTO_PARSE_SAVED_PDF_LIMIT
        and parse_execution_name != "none"
        and _saved_pdf_batch_prompt_enabled()
    ):
        batch_window = _saved_pdf_batch_window_seconds()
        recent_papers = _recent_saved_pdf_papers(save_path, window_seconds=batch_window, searchers=searchers)
        if len(recent_papers) > AUTO_PARSE_SAVED_PDF_LIMIT:
            prompt_papers = recent_papers
            prompt_query = f"recent saved PDFs in {resolve_save_path(save_path)}"
            prompt_sources = "local"
            prompt_parse_execution = "prompt"
            batch_prompt_metadata = {
                "trigger": "saved_pdf_batch_threshold",
                "batch_window_seconds": batch_window,
                "auto_parse_limit": AUTO_PARSE_SAVED_PDF_LIMIT,
                "message": (
                    f"{len(recent_papers)} recent PDFs are saved in this directory, above the "
                    f"auto-parse limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. Select PDFs before MinerU parsing."
                ),
            }

    parse_prompt: Dict[str, Any] = {}
    if after_save_prompt_hook is not None:
        parse_prompt = await after_save_prompt_hook(
            papers=prompt_papers,
            query=prompt_query,
            sources=prompt_sources,
            save_path=save_path,
            ctx=ctx,
            parse_execution=prompt_parse_execution,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )
    else:
        parse_prompt = {
            "status": "no_prompt_hook",
            "message": "No parse-prompt hook configured. PDF saved.",
            "papers": prompt_papers,
            "total": len(prompt_papers),
        }

    if batch_prompt_metadata and isinstance(parse_prompt, dict):
        parse_prompt.update(batch_prompt_metadata)

    response: Dict[str, Any] = {
        "status": "downloaded",
        "pdf_path": pdf_paths[0],
        "pdf_paths": pdf_paths,
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "title": title or Path(pdf_paths[0]).stem,
        "parse_prompt": parse_prompt,
    }
    if isinstance(parse_prompt, dict) and isinstance(parse_prompt.get("app"), dict):
        response["app"] = parse_prompt["app"]

    return response


async def _after_saved_pdfs(
    pdf_paths: List[str],
    *,
    source: str,
    paper_id: str,
    save_path: str,
    downloader: str,
    doi: str = "",
    title: str = "",
    legal_status: str = "source_native_or_open_access",
    ctx: Any = None,
    parse_execution: str = "background",
    custom_save_path_confirmed: bool = False,
    after_save_prompt_hook: Any = None,
    searchers: Optional[Dict[str, Any]] = None,
    _attach_local_selection_ui_fn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Record multiple downloaded PDFs and optionally trigger a parse prompt."""
    normalized: List[str] = []
    for pdf_path in pdf_paths:
        resolved = _pdf_path_from_result(pdf_path)
        if resolved and resolved not in normalized:
            normalized.append(resolved)
    if not normalized:
        return None
    return await _after_saved_pdf(
        {"pdf_paths": normalized},
        source=source,
        paper_id=paper_id,
        save_path=save_path,
        downloader=downloader,
        doi=doi,
        title=title,
        legal_status=legal_status,
        ctx=ctx,
        parse_execution=parse_execution,
        custom_save_path_confirmed=custom_save_path_confirmed,
        after_save_prompt_hook=after_save_prompt_hook,
        searchers=searchers,
        _attach_local_selection_ui_fn=_attach_local_selection_ui_fn,
    )


# ---------------------------------------------------------------------------
# Wrap searcher save_path defaults
# ---------------------------------------------------------------------------


def _mcp_save_path_metadata(save_path: str, *, custom_save_path_confirmed: bool = False) -> Dict[str, Any]:
    resolved = resolve_save_path(save_path)
    default = resolve_save_path(DEFAULT_SAVE_PATH)
    return {
        "save_path": resolved,
        "default_save_path": default,
        "save_path_defaulted": resolved == default,
        "custom_save_path_confirmed": bool(custom_save_path_confirmed),
    }

def _wrap_save_path_methods(searcher: Any) -> None:
    """Expand ~/Desktop/papers-style defaults before source connectors touch paths."""
    if searcher is None or getattr(searcher, "_paper_search_save_path_wrapped", False):
        return
    for method_name in ("download_pdf", "read_paper"):
        original = getattr(searcher, method_name, None)
        if not callable(original):
            continue

        def _make_wrapper(method):
            def _wrapped(paper_id, save_path=DEFAULT_SAVE_PATH, *args, **kwargs):
                return method(paper_id, resolve_save_path(save_path), *args, **kwargs)
            return _wrapped

        setattr(searcher, method_name, _make_wrapper(original))
    setattr(searcher, "_paper_search_save_path_wrapped", True)


# ---------------------------------------------------------------------------
# Re-imports from engine.parse (canonical sources for these helpers)
# Placed at bottom to avoid circular imports.
# ---------------------------------------------------------------------------
from .parse import _env_flag_enabled, _selection_semantics_name, _workflow_parse_execution_name  # noqa: E402,F401
