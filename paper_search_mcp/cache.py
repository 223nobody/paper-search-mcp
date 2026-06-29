from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_env


DEFAULT_CACHE_DIR = ".paper_search_cache"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_root(cache_dir: Optional[str] = None) -> Path:
    configured = cache_dir or get_env("CACHE_DIR", DEFAULT_CACHE_DIR)
    return Path(configured).expanduser().resolve()


def papers_root(cache_dir: Optional[str] = None) -> Path:
    root = cache_root(cache_dir) / "papers"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sessions_root(cache_dir: Optional[str] = None) -> Path:
    root = cache_root(cache_dir) / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def jobs_root(cache_dir: Optional[str] = None) -> Path:
    root = cache_root(cache_dir) / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def mineru_batches_root(cache_dir: Optional[str] = None) -> Path:
    root = cache_root(cache_dir) / "mineru_batches"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_slug(value: str, default: str = "paper") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip()).strip("._")
    return (normalized or default)[:140]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def paper_key(
    *,
    paper_key_hint: str = "",
    doi: str = "",
    source: str = "",
    paper_id: str = "",
    title: str = "",
    pdf_path: str = "",
) -> str:
    if paper_key_hint:
        return safe_slug(paper_key_hint)

    normalized_doi = doi.strip().lower()
    if normalized_doi:
        return "doi_" + safe_slug(normalized_doi)

    normalized_source = source.strip().lower()
    normalized_id = paper_id.strip()
    if normalized_source and normalized_id:
        return safe_slug(f"{normalized_source}_{normalized_id}")

    if title.strip():
        return "title_" + safe_slug(title)[:80] + "_" + short_hash(title.strip().lower(), 8)

    if pdf_path:
        path = Path(pdf_path)
        if path.exists():
            return "pdf_" + sha256_file(path)[:16]
        return "pdf_" + short_hash(str(path.resolve()), 16)

    return "paper_" + short_hash(utc_now(), 12)


def paper_dir(key: str, cache_dir: Optional[str] = None) -> Path:
    directory = papers_root(cache_dir) / safe_slug(key)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_json(path: str | Path, default: Any = None) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def visible_artifact_paths(pdf_path: str | Path) -> Dict[str, str]:
    pdf = Path(pdf_path).expanduser().resolve()
    export_dir = (pdf.parent / f"{pdf.stem}_mineru").resolve()
    return {
        "export_dir": str(export_dir),
        "full_md": str(export_dir / "full.md"),
        "content_list": str(export_dir / "content_list.json"),
        "manifest": str(export_dir / "manifest.json"),
        "metadata": str(export_dir / "metadata.json"),
        "status": str(export_dir / "status.json"),
        "assets_dir": str(export_dir / "assets"),
        "result_zip": str(pdf.with_suffix(".zip").resolve()),
    }


def _visible_paths_from_metadata(metadata: Dict[str, Any]) -> Dict[str, str]:
    pdf_path = str(metadata.get("pdf_path") or "").strip()
    if not pdf_path:
        return {}
    return visible_artifact_paths(pdf_path)


def resolved_parsed_paths(key: str, cache_dir: Optional[str] = None) -> Dict[str, str]:
    paths = get_cached_paths(key, cache_dir)
    metadata = read_json(paths["metadata"], {}) or {}
    visible_paths = _visible_paths_from_metadata(metadata) if isinstance(metadata, dict) else {}
    if not visible_paths:
        return paths

    resolved = dict(paths)
    pdf_path = str(metadata.get("pdf_path") or "").strip()
    if pdf_path:
        resolved["pdf_path"] = str(Path(pdf_path).expanduser().resolve())
    for name in ("full_md", "content_list", "manifest", "assets_dir"):
        visible = visible_paths.get(name)
        if visible and Path(visible).exists():
            resolved[name] = visible
    for name in ("export_dir", "status", "metadata", "result_zip"):
        visible = visible_paths.get(name)
        if visible:
            resolved[f"visible_{name}"] = visible
    return resolved


def copy_pdf_to_cache(pdf_path: str | Path, key: str, cache_dir: Optional[str] = None) -> Path:
    """Compatibility wrapper: validate and return the source PDF path.

    Older versions copied PDFs into ``.paper_search_cache`` as ``source.pdf``.
    The cache now stores metadata and indexes only; user-visible artifacts live
    beside the PDF.
    """
    source = Path(pdf_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"PDF not found: {source}")
    paper_dir(key, cache_dir)
    return source


def record_download(
    *,
    pdf_path: str,
    paper_key_hint: str = "",
    source: str = "",
    paper_id: str = "",
    doi: str = "",
    title: str = "",
    downloader: str = "",
    legal_status: str = "unknown",
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    key = paper_key(
        paper_key_hint=paper_key_hint,
        doi=doi,
        source=source,
        paper_id=paper_id,
        title=title,
        pdf_path=pdf_path,
    )
    directory = paper_dir(key, cache_dir)
    payload = {
        "paper_key": key,
        "pdf_path": str(Path(pdf_path).expanduser().resolve()),
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "title": title,
        "downloader": downloader,
        "legal_status": legal_status,
        "recorded_at": utc_now(),
    }
    try:
        payload["pdf_sha256"] = sha256_file(pdf_path)
    except Exception:
        payload["pdf_sha256"] = ""

    existing = read_json(directory / "metadata.json", {})
    if isinstance(existing, dict):
        existing.update({k: v for k, v in payload.items() if v})
        payload = existing

    write_json(directory / "metadata.json", payload)
    return payload


def download_health_path(cache_dir: Optional[str] = None) -> Path:
    return cache_root(cache_dir) / "download_health.json"


def record_download_health(
    *,
    method: str,
    source: str = "",
    ok: bool,
    elapsed_seconds: float = 0.0,
    error: str = "",
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    method_name = safe_slug(method or "download", default="download")
    source_name = safe_slug(source or "global", default="global")
    path = download_health_path(cache_dir)
    payload = read_json(path, {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    methods = payload.setdefault("methods", {})
    key = f"{source_name}:{method_name}"
    entry = methods.get(key, {})
    if not isinstance(entry, dict):
        entry = {}

    attempts = int(entry.get("attempts") or 0) + 1
    successes = int(entry.get("successes") or 0) + (1 if ok else 0)
    failures = int(entry.get("failures") or 0) + (0 if ok else 1)
    total_elapsed = float(entry.get("total_elapsed_seconds") or 0.0) + max(0.0, float(elapsed_seconds or 0.0))
    entry.update(
        {
            "method": method_name,
            "source": source_name,
            "attempts": attempts,
            "successes": successes,
            "failures": failures,
            "success_rate": round(successes / attempts, 4) if attempts else 0,
            "avg_elapsed_seconds": round(total_elapsed / attempts, 4) if attempts else 0,
            "total_elapsed_seconds": round(total_elapsed, 4),
            "last_status": "ok" if ok else "error",
            "last_error": "" if ok else str(error or ""),
            "updated_at": utc_now(),
        }
    )
    methods[key] = entry
    payload["updated_at"] = utc_now()
    write_json(path, payload)
    return entry


def get_download_health(cache_dir: Optional[str] = None) -> Dict[str, Any]:
    payload = read_json(download_health_path(cache_dir), {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("methods", {})
    payload["path"] = str(download_health_path(cache_dir))
    return payload


def rank_download_methods(
    methods: List[str],
    *,
    source: str = "",
    cache_dir: Optional[str] = None,
) -> List[str]:
    health = get_download_health(cache_dir)
    stats = health.get("methods", {}) if isinstance(health, dict) else {}
    source_name = safe_slug(source or "global", default="global")

    def score(method: str) -> tuple[float, int]:
        key = f"{source_name}:{safe_slug(method, default='download')}"
        entry = stats.get(key, {}) if isinstance(stats, dict) else {}
        attempts = int(entry.get("attempts") or 0) if isinstance(entry, dict) else 0
        if attempts <= 0:
            return (0.5, 0)
        success_rate = float(entry.get("success_rate") or 0.0)
        avg_elapsed = float(entry.get("avg_elapsed_seconds") or 0.0)
        return (success_rate - min(avg_elapsed / 120.0, 0.25), -attempts)

    return sorted(methods, key=score, reverse=True)


def list_parsed(cache_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    root = papers_root(cache_dir)
    entries: List[Dict[str, Any]] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        metadata = read_json(directory / "metadata.json", {}) or {}
        manifest = read_json(directory / "mineru" / "manifest.json", {}) or {}
        status = read_json(directory / "status.json", {}) or {}
        paths = resolved_parsed_paths(directory.name, cache_dir)
        full_md = Path(paths["full_md"])
        content_list = Path(paths["content_list"])
        visible_dir = paths.get("visible_export_dir", "")
        entries.append(
            {
                "paper_key": directory.name,
                "title": metadata.get("title", ""),
                "doi": metadata.get("doi", ""),
                "source": metadata.get("source", ""),
                "paper_id": metadata.get("paper_id", ""),
                "parsed": full_md.exists() or content_list.exists(),
                "parser": manifest.get("parser", ""),
                "backend": manifest.get("backend", ""),
                "status": status.get("status", ""),
                "updated_at": manifest.get("created_at") or metadata.get("recorded_at", ""),
                "path": str(directory),
                "visible_path": visible_dir,
            }
        )
    return entries


def find_download_by_pdf_path(pdf_path: str | Path, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    target = str(Path(pdf_path).expanduser().resolve())
    root = papers_root(cache_dir)
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        metadata = read_json(directory / "metadata.json", {}) or {}
        if not isinstance(metadata, dict):
            continue
        stored_path = str(metadata.get("pdf_path") or "").strip()
        if not stored_path:
            continue
        try:
            if str(Path(stored_path).expanduser().resolve()) == target:
                return metadata
        except OSError:
            continue
    return {}


def get_cached_paths(key: str, cache_dir: Optional[str] = None) -> Dict[str, str]:
    directory = paper_dir(key, cache_dir)
    mineru_dir = directory / "mineru"
    return {
        "paper_dir": str(directory),
        "metadata": str(directory / "metadata.json"),
        "status": str(directory / "status.json"),
        "source_pdf": str(directory / "source.pdf"),
        "mineru_dir": str(mineru_dir),
        "full_md": str(mineru_dir / "full.md"),
        "content_list": str(mineru_dir / "content_list.json"),
        "manifest": str(mineru_dir / "manifest.json"),
        "assets_dir": str(mineru_dir / "assets"),
    }


def read_parsed(key: str, output_format: str = "markdown", cache_dir: Optional[str] = None) -> Any:
    paths = resolved_parsed_paths(key, cache_dir)
    fmt = output_format.strip().lower()
    if fmt in {"markdown", "md"}:
        path = Path(paths["full_md"])
        return path.read_text(encoding="utf-8") if path.exists() else ""
    if fmt in {"json", "content_list", "content"}:
        return read_json(paths["content_list"], [])
    if fmt == "manifest":
        return read_json(paths["manifest"], {})
    if fmt == "metadata":
        return read_json(paths["metadata"], {})
    if fmt == "paths":
        return paths
    raise ValueError(f"Unsupported parsed output format: {output_format}")


def list_assets(key: str, asset_type: str = "all", cache_dir: Optional[str] = None) -> List[Dict[str, str]]:
    paths = resolved_parsed_paths(key, cache_dir)
    assets_dir = Path(paths["assets_dir"])
    if not assets_dir.exists():
        return []

    wanted = asset_type.strip().lower()
    assets: List[Dict[str, str]] = []
    for path in sorted(assets_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(assets_dir)
        inferred = rel.parts[0].lower() if len(rel.parts) > 1 else "asset"
        if wanted not in {"all", inferred, inferred.rstrip("s")}:
            continue
        assets.append(
            {
                "type": inferred,
                "name": path.name,
                "path": str(path),
                "relative_path": str(rel).replace("\\", "/"),
            }
        )
    return assets


def parsed_index_path(cache_dir: Optional[str] = None) -> Path:
    return cache_root(cache_dir) / "parsed_index.sqlite3"


def _connect_parsed_index(cache_dir: Optional[str] = None) -> sqlite3.Connection:
    path = parsed_index_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_parsed_index(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS parsed_blocks USING fts5(
                paper_key UNINDEXED,
                block_id UNINDEXED,
                type UNINDEXED,
                page UNINDEXED,
                ord UNINDEXED,
                text,
                title,
                doi,
                source
            )
            """
        )
        connection.commit()
        return True
    except sqlite3.Error:
        return False


def _parsed_block_text(item: Dict[str, Any]) -> str:
    for name in ("text", "markdown", "content"):
        value = item.get(name)
        if value:
            return str(value)
    return ""


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+|[\u4e00-\u9fff]+", query, flags=re.UNICODE)
    if not tokens:
        escaped = query.replace('"', '""')
        return f'"{escaped}"'
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _snippet_from_text(text: str, query: str, radius: int = 160) -> str:
    if not text:
        return ""
    needle = query.strip().lower()
    haystack = text.lower()
    index = haystack.find(needle) if needle else -1
    if index < 0:
        return text[: radius * 2].strip()
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    return text[start:end].strip()


def index_parsed_paper(key: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """Index one parsed paper into a lightweight SQLite FTS table."""
    with closing(_connect_parsed_index(cache_dir)) as connection:
        if not _ensure_parsed_index(connection):
            return {
                "status": "unavailable",
                "paper_key": key,
                "indexed": 0,
                "index_path": str(parsed_index_path(cache_dir)),
                "message": "SQLite FTS5 is not available.",
            }

        metadata = read_parsed(key, "metadata", cache_dir)
        if not isinstance(metadata, dict):
            metadata = {}
        content = read_parsed(key, "json", cache_dir)
        rows: List[Dict[str, Any]] = []
        if isinstance(content, list):
            for index, item in enumerate(content):
                if not isinstance(item, dict):
                    continue
                text = _parsed_block_text(item)
                if not text:
                    continue
                rows.append(
                    {
                        "paper_key": key,
                        "block_id": str(item.get("id", "")),
                        "type": str(item.get("type", "")),
                        "page": str(item.get("page", "")),
                        "ord": str(item.get("order", index)),
                        "text": text,
                        "title": str(metadata.get("title", "")),
                        "doi": str(metadata.get("doi", "")),
                        "source": str(metadata.get("source", "")),
                    }
                )

        if not rows:
            markdown = read_parsed(key, "markdown", cache_dir)
            if isinstance(markdown, str) and markdown.strip():
                rows.append(
                    {
                        "paper_key": key,
                        "block_id": "",
                        "type": "markdown",
                        "page": "",
                        "ord": "0",
                        "text": markdown,
                        "title": str(metadata.get("title", "")),
                        "doi": str(metadata.get("doi", "")),
                        "source": str(metadata.get("source", "")),
                    }
                )

        connection.execute("DELETE FROM parsed_blocks WHERE paper_key = ?", (key,))
        if rows:
            connection.executemany(
                """
                INSERT INTO parsed_blocks
                    (paper_key, block_id, type, page, ord, text, title, doi, source)
                VALUES
                    (:paper_key, :block_id, :type, :page, :ord, :text, :title, :doi, :source)
                """,
                rows,
            )
        connection.commit()

    return {
        "status": "ok",
        "paper_key": key,
        "indexed": len(rows),
        "index_path": str(parsed_index_path(cache_dir)),
    }


def rebuild_parsed_index(cache_dir: Optional[str] = None) -> Dict[str, Any]:
    with closing(_connect_parsed_index(cache_dir)) as connection:
        if not _ensure_parsed_index(connection):
            return {
                "status": "unavailable",
                "indexed": 0,
                "papers": 0,
                "index_path": str(parsed_index_path(cache_dir)),
                "message": "SQLite FTS5 is not available.",
            }
        connection.execute("DELETE FROM parsed_blocks")
        connection.commit()

    indexed = 0
    papers = 0
    errors: Dict[str, str] = {}
    for entry in list_parsed(cache_dir):
        key = str(entry.get("paper_key") or "")
        if not key or not entry.get("parsed"):
            continue
        papers += 1
        try:
            result = index_parsed_paper(key, cache_dir)
            indexed += int(result.get("indexed") or 0)
        except Exception as exc:
            errors[key] = str(exc)

    return {
        "status": "ok" if not errors else "partial",
        "indexed": indexed,
        "papers": papers,
        "errors": errors,
        "index_path": str(parsed_index_path(cache_dir)),
    }


def search_parsed_index(
    query: str,
    paper_key: str = "",
    max_results: int = 20,
    cache_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    needle = query.strip()
    if not needle:
        return []

    try:
        with closing(_connect_parsed_index(cache_dir)) as connection:
            if not _ensure_parsed_index(connection):
                raise sqlite3.OperationalError("SQLite FTS5 is not available")

            where = "parsed_blocks MATCH ?"
            params: List[Any] = [_fts_query(needle)]
            if paper_key:
                where += " AND paper_key = ?"
                params.append(safe_slug(paper_key))
            params.append(max(1, int(max_results)))
            rows = connection.execute(
                f"""
                SELECT
                    paper_key,
                    block_id,
                    type,
                    page,
                    ord,
                    text,
                    snippet(parsed_blocks, 5, '', '', '...', 32) AS snippet,
                    bm25(parsed_blocks) AS rank
                FROM parsed_blocks
                WHERE {where}
                ORDER BY rank
                LIMIT ?
                """,
                params,
            ).fetchall()
    except sqlite3.Error:
        if paper_key:
            return search_parsed(paper_key, query, max_results, cache_dir)
        hits: List[Dict[str, Any]] = []
        for entry in list_parsed(cache_dir):
            key = str(entry.get("paper_key") or "")
            if not key or not entry.get("parsed"):
                continue
            for hit in search_parsed(key, query, max_results - len(hits), cache_dir):
                hit["paper_key"] = key
                hits.append(hit)
                if len(hits) >= max_results:
                    return hits
        return hits

    hits = []
    for row in rows:
        text = str(row["text"] or "")
        snippet = str(row["snippet"] or "").strip() or _snippet_from_text(text, query)
        hits.append(
            {
                "paper_key": row["paper_key"],
                "block_id": row["block_id"],
                "type": row["type"],
                "page": row["page"],
                "order": row["ord"],
                "snippet": snippet,
                "rank": row["rank"],
            }
        )
    return hits


def search_parsed(key: str, query: str, max_results: int = 20, cache_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    needle = query.strip().lower()
    if not needle:
        return []

    hits: List[Dict[str, Any]] = []
    content = read_parsed(key, "json", cache_dir)
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("markdown") or item.get("content") or "")
            haystack = text.lower()
            if needle not in haystack:
                continue
            index = haystack.find(needle)
            start = max(0, index - 160)
            end = min(len(text), index + len(query) + 160)
            hits.append(
                {
                    "block_id": str(item.get("id", "")),
                    "type": str(item.get("type", "")),
                    "page": item.get("page", ""),
                    "order": item.get("order", len(hits)),
                    "snippet": text[start:end].strip(),
                }
            )
            if len(hits) >= max_results:
                return hits

    markdown = read_parsed(key, "markdown", cache_dir)
    if isinstance(markdown, str) and markdown and not hits:
        lower = markdown.lower()
        start = 0
        while len(hits) < max_results:
            index = lower.find(needle, start)
            if index < 0:
                break
            snippet_start = max(0, index - 160)
            snippet_end = min(len(markdown), index + len(query) + 160)
            hits.append(
                {
                    "block_id": "",
                    "type": "markdown",
                    "page": "",
                    "order": len(hits),
                    "snippet": markdown[snippet_start:snippet_end].strip(),
                }
            )
            start = index + len(query)
    return hits


def delete_cache(key: str, cache_dir: Optional[str] = None) -> bool:
    directory = papers_root(cache_dir) / safe_slug(key)
    if not directory.exists():
        return False
    try:
        with closing(_connect_parsed_index(cache_dir)) as connection:
            if _ensure_parsed_index(connection):
                connection.execute("DELETE FROM parsed_blocks WHERE paper_key = ?", (safe_slug(key),))
                connection.commit()
    except sqlite3.Error:
        pass
    shutil.rmtree(directory)
    return True


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
        return total
    return 0


def _same_existing_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists() or not left.is_file() or not right.is_file():
        return False
    try:
        if left.resolve() == right.resolve():
            return False
        if left.stat().st_size != right.stat().st_size:
            return False
        return sha256_file(left) == sha256_file(right)
    except OSError:
        return False


def _cleanup_candidate(path: Path, *, reason: str, dry_run: bool) -> Dict[str, Any]:
    if not path.exists():
        return {}

    size = _path_size(path)
    entry = {
        "path": str(path),
        "kind": "directory" if path.is_dir() else "file",
        "bytes": size,
        "reason": reason,
        "deleted": False,
    }
    if not dry_run:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        entry["deleted"] = True
    return entry


def cleanup_redundant_artifacts(cache_dir: Optional[str] = None, *, dry_run: bool = True) -> Dict[str, Any]:
    """Remove historical heavyweight cache duplicates while preserving indexes.

    The current parser writes normalized outputs beside the source PDF and keeps
    only metadata/status/manifest indexes in the cache. This cleaner targets
    old cache layouts that stored PDFs, parsed Markdown/JSON/assets, or raw
    MinerU intermediates under ``.paper_search_cache``.
    """
    root = papers_root(cache_dir)
    removed: List[Dict[str, Any]] = []
    preserved: List[Dict[str, Any]] = []

    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue

        key = directory.name
        paths = get_cached_paths(key, cache_dir)
        metadata = read_json(paths["metadata"], {}) or {}
        visible_paths = _visible_paths_from_metadata(metadata) if isinstance(metadata, dict) else {}

        for raw_name in ("raw", "raw_cli"):
            candidate = Path(paths["mineru_dir"]) / raw_name
            entry = _cleanup_candidate(
                candidate,
                reason=f"legacy MinerU {raw_name} intermediate output",
                dry_run=dry_run,
            )
            if entry:
                entry["paper_key"] = key
                removed.append(entry)

        paired_files = (
            ("full_md", "legacy parsed Markdown duplicate"),
            ("content_list", "legacy parsed content_list duplicate"),
            ("manifest", "legacy parsed manifest duplicate"),
        )
        for name, reason in paired_files:
            cached_path = Path(paths[name])
            visible_value = visible_paths.get(name)
            if not visible_value:
                continue
            visible_path = Path(visible_value)
            if visible_path.exists() and cached_path.exists() and cached_path.resolve() != visible_path.resolve():
                entry = _cleanup_candidate(cached_path, reason=reason, dry_run=dry_run)
                if entry:
                    entry["paper_key"] = key
                    removed.append(entry)

        cached_assets = Path(paths["assets_dir"])
        visible_assets_value = visible_paths.get("assets_dir")
        visible_assets = Path(visible_assets_value) if visible_assets_value else None
        if (
            visible_assets is not None
            and visible_assets.exists()
            and cached_assets.exists()
            and cached_assets.is_dir()
            and cached_assets.resolve() != visible_assets.resolve()
        ):
            entry = _cleanup_candidate(
                cached_assets,
                reason="legacy parsed assets duplicate",
                dry_run=dry_run,
            )
            if entry:
                entry["paper_key"] = key
                removed.append(entry)

        source_pdf = Path(paths["source_pdf"])
        metadata_pdf = Path(str(metadata.get("pdf_path") or "")).expanduser() if isinstance(metadata, dict) else Path("")
        if source_pdf.exists():
            if _same_existing_file(source_pdf, metadata_pdf):
                entry = _cleanup_candidate(
                    source_pdf,
                    reason="legacy cached source.pdf duplicate of recorded PDF",
                    dry_run=dry_run,
                )
                if entry:
                    entry["paper_key"] = key
                    removed.append(entry)
            else:
                preserved.append(
                    {
                        "paper_key": key,
                        "path": str(source_pdf),
                        "kind": "file",
                        "bytes": _path_size(source_pdf),
                        "reason": "source.pdf was not removed because no distinct matching recorded PDF was found",
                    }
                )

    return {
        "status": "dry_run" if dry_run else "ok",
        "cache_root": str(cache_root(cache_dir)),
        "dry_run": dry_run,
        "removed": removed,
        "preserved": preserved,
        "removed_total": len(removed),
        "preserved_total": len(preserved),
        "bytes_reclaimable": sum(int(item.get("bytes", 0)) for item in removed),
        "bytes_deleted": 0 if dry_run else sum(int(item.get("bytes", 0)) for item in removed),
    }


def cleanup_stale_cache_entries(cache_dir: Optional[str] = None, *, dry_run: bool = True) -> Dict[str, Any]:
    """Remove lightweight paper indexes whose PDF-side artifacts are gone.

    A parsed-paper entry is stale when the recorded PDF path does not exist and
    no resolved Markdown/content/assets artifact exists either. The cleaner
    leaves entries with any surviving user-visible artifact in place.
    """
    root = papers_root(cache_dir)
    candidates: List[Dict[str, Any]] = []
    preserved: List[Dict[str, Any]] = []

    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue

        key = directory.name
        paths = resolved_parsed_paths(key, cache_dir)
        metadata = read_json(paths["metadata"], {}) or {}
        pdf_path = str(metadata.get("pdf_path") or paths.get("pdf_path") or "").strip()
        pdf_exists = bool(pdf_path) and Path(pdf_path).expanduser().exists()
        artifact_names = ("full_md", "content_list", "assets_dir")
        existing_artifacts = [
            name
            for name in artifact_names
            if paths.get(name) and Path(paths[name]).expanduser().exists()
        ]

        entry = {
            "paper_key": key,
            "path": str(directory),
            "bytes": _path_size(directory),
            "pdf_path": pdf_path,
            "pdf_exists": pdf_exists,
            "existing_artifacts": existing_artifacts,
        }
        if pdf_exists or existing_artifacts:
            preserved.append(
                {
                    **entry,
                    "reason": "preserved because recorded PDF or parsed artifacts still exist",
                }
            )
            continue

        candidates.append(
            {
                **entry,
                "reason": "recorded PDF and parsed artifacts are missing",
                "deleted": False,
            }
        )

    index_vacuumed = False
    index_cleanup_error = ""
    if not dry_run:
        deleted_keys: List[str] = []
        for item in candidates:
            directory = Path(str(item["path"]))
            if directory.exists():
                shutil.rmtree(directory)
            item["deleted"] = True
            deleted_keys.append(str(item["paper_key"]))

        if deleted_keys:
            try:
                with closing(_connect_parsed_index(cache_dir)) as connection:
                    if _ensure_parsed_index(connection):
                        connection.executemany(
                            "DELETE FROM parsed_blocks WHERE paper_key = ?",
                            [(safe_slug(key),) for key in deleted_keys],
                        )
                        connection.commit()
                        connection.execute("VACUUM")
                        index_vacuumed = True
            except sqlite3.Error:
                index_cleanup_error = "Failed to clean or vacuum parsed_index.sqlite3."

    return {
        "status": "dry_run" if dry_run else "ok",
        "cache_root": str(cache_root(cache_dir)),
        "dry_run": dry_run,
        "stale": candidates,
        "preserved": preserved,
        "stale_total": len(candidates),
        "preserved_total": len(preserved),
        "bytes_reclaimable": sum(int(item.get("bytes", 0)) for item in candidates),
        "bytes_deleted": 0 if dry_run else sum(int(item.get("bytes", 0)) for item in candidates),
        "index_vacuumed": index_vacuumed,
        "index_cleanup_error": index_cleanup_error,
    }


def _session_path(selection_token: str, cache_dir: Optional[str] = None) -> Path:
    token = safe_slug(selection_token, default="session")
    return sessions_root(cache_dir) / f"{token}.json"


def create_search_session(
    query: str,
    sources: str,
    papers: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    created_at = utc_now()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    token_seed = f"{created_at}|{query}|{sources}|{len(papers)}"
    selection_token = f"search_{stamp}_{short_hash(token_seed, 8)}"
    payload = {
        "selection_token": selection_token,
        "query": query,
        "sources": sources,
        "created_at": created_at,
        "updated_at": created_at,
        "papers": papers,
        "metadata": metadata or {},
    }
    write_json(_session_path(selection_token, cache_dir), payload)
    return payload


def get_search_session(selection_token: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    return read_json(_session_path(selection_token, cache_dir), {}) or {}


def update_search_session_metadata(
    selection_token: str,
    updates: Dict[str, Any],
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    session = get_search_session(selection_token, cache_dir)
    if not session:
        return {}
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(updates)
    session["metadata"] = metadata
    session["updated_at"] = utc_now()
    write_json(_session_path(selection_token, cache_dir), session)
    return session


def list_search_sessions(cache_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    root = sessions_root(cache_dir)
    sessions: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        session = read_json(path, {}) or {}
        if not isinstance(session, dict):
            continue
        papers = session.get("papers", [])
        sessions.append(
            {
                "selection_token": session.get("selection_token", path.stem),
                "query": session.get("query", ""),
                "sources": session.get("sources", ""),
                "created_at": session.get("created_at", ""),
                "updated_at": session.get("updated_at", ""),
                "total": len(papers) if isinstance(papers, list) else 0,
                "path": str(path),
                "metadata": session.get("metadata", {}),
            }
        )
    sessions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return sessions


def delete_search_session(selection_token: str, cache_dir: Optional[str] = None) -> bool:
    path = _session_path(selection_token, cache_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


def cleanup_expired_sessions(
    cache_dir: Optional[str] = None,
    *,
    ttl_hours: Optional[float] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Delete search sessions older than *ttl_hours* (default 24 h).

    Reads ``PAPER_SEARCH_MCP_SESSION_TTL_HOURS`` from the environment.
    Set to ``0`` or a negative value to disable automatic cleanup.

    Returns a dict with ``deleted_count`` and ``status``.
    """
    if ttl_hours is None:
        raw = get_env("SESSION_TTL_HOURS", "24").strip()
        try:
            ttl_hours = float(raw) if raw else 24.0
        except ValueError:
            ttl_hours = 24.0

    if ttl_hours <= 0:
        return {
            "status": "disabled",
            "ttl_hours": ttl_hours,
            "message": (
                "Session TTL cleanup is disabled "
                "(PAPER_SEARCH_MCP_SESSION_TTL_HOURS <= 0)."
            ),
        }

    from datetime import timedelta

    root = sessions_root(cache_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    deleted_count = 0
    preserved_count = 0

    for path in sorted(root.glob("*.json")):
        session = read_json(path, {}) or {}
        if not isinstance(session, dict):
            continue

        created_at_str = str(session.get("created_at", ""))
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except (ValueError, TypeError):
            # Fall back to file modification time
            try:
                created_at = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                )
            except OSError:
                preserved_count += 1
                continue

        if created_at < cutoff:
            if not dry_run:
                path.unlink(missing_ok=True)
            deleted_count += 1
        else:
            preserved_count += 1

    if deleted_count:
        logging.getLogger(__name__).info(
            "Session TTL cleanup: %d session(s) older than %.1f h %s.",
            deleted_count,
            ttl_hours,
            "(dry run)" if dry_run else "deleted",
        )

    return {
        "status": "dry_run" if dry_run else "ok",
        "ttl_hours": ttl_hours,
        "deleted_count": deleted_count,
        "preserved_count": preserved_count,
    }


# ── Download state persistence (checkpoint/resume) ──────────────────────

def _session_download_state_path(selection_token: str, cache_dir: Optional[str] = None) -> Path:
    token = safe_slug(selection_token, default="session")
    return sessions_root(cache_dir) / f"{token}_download_state.json"


def read_session_download_state(selection_token: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    payload = read_json(_session_download_state_path(selection_token, cache_dir), {})
    return payload if isinstance(payload, dict) else {}


def write_session_download_state(selection_token: str, payload: Dict[str, Any], cache_dir: Optional[str] = None) -> Dict[str, Any]:
    stored = dict(payload)
    stored["selection_token"] = selection_token
    stored["updated_at"] = utc_now()
    write_json(_session_download_state_path(selection_token, cache_dir), stored)
    return stored


def delete_session_download_state(selection_token: str, cache_dir: Optional[str] = None) -> bool:
    path = _session_download_state_path(selection_token, cache_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


# ── Parse-prompt state persistence (downloaded PDFs -> optional MinerU) ────

def _selection_ui_state_path(selection_token: str, cache_dir: Optional[str] = None) -> Path:
    token = safe_slug(selection_token, default="session")
    return sessions_root(cache_dir) / f"{token}_ui_state.json"


def read_selection_ui_state(selection_token: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    payload = read_json(_selection_ui_state_path(selection_token, cache_dir), {})
    return payload if isinstance(payload, dict) else {}


def write_selection_ui_state(
    selection_token: str,
    payload: Dict[str, Any],
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    stored = dict(payload)
    stored["selection_token"] = selection_token
    stored["updated_at"] = utc_now()
    write_json(_selection_ui_state_path(selection_token, cache_dir), stored)
    return stored


def delete_selection_ui_state(selection_token: str, cache_dir: Optional[str] = None) -> bool:
    path = _selection_ui_state_path(selection_token, cache_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


def _parse_prompt_state_path(selection_token: str, cache_dir: Optional[str] = None) -> Path:
    token = safe_slug(selection_token, default="session")
    return sessions_root(cache_dir) / f"{token}_parse_prompt_state.json"


def read_parse_prompt_state(selection_token: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    payload = read_json(_parse_prompt_state_path(selection_token, cache_dir), {})
    return payload if isinstance(payload, dict) else {}


def write_parse_prompt_state(selection_token: str, payload: Dict[str, Any], cache_dir: Optional[str] = None) -> Dict[str, Any]:
    stored = dict(payload)
    stored["download_selection_token"] = selection_token
    stored["updated_at"] = utc_now()
    write_json(_parse_prompt_state_path(selection_token, cache_dir), stored)
    return stored


def delete_parse_prompt_state(selection_token: str, cache_dir: Optional[str] = None) -> bool:
    path = _parse_prompt_state_path(selection_token, cache_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


def _parse_job_path(job_id: str, cache_dir: Optional[str] = None) -> Path:
    return jobs_root(cache_dir) / f"{safe_slug(job_id, default='parse_job')}.json"


def write_parse_job(job_id: str, payload: Dict[str, Any], cache_dir: Optional[str] = None) -> Dict[str, Any]:
    stored = dict(payload)
    stored["job_id"] = job_id
    stored["updated_at"] = stored.get("updated_at") or utc_now()
    write_json(_parse_job_path(job_id, cache_dir), stored)
    return stored


def read_parse_job(job_id: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    payload = read_json(_parse_job_path(job_id, cache_dir), {}) or {}
    return payload if isinstance(payload, dict) else {}


def list_parse_job_records(cache_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in sorted(jobs_root(cache_dir).glob("*.json")):
        payload = read_json(path, {}) or {}
        if not isinstance(payload, dict):
            continue
        payload.setdefault("job_id", path.stem)
        records.append(payload)
    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return records


def mineru_batch_path(batch_key: str, cache_dir: Optional[str] = None) -> Path:
    return mineru_batches_root(cache_dir) / f"{safe_slug(batch_key, default='mineru_batch')}.json"


def read_mineru_batch(batch_key: str, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    payload = read_json(mineru_batch_path(batch_key, cache_dir), {}) or {}
    return payload if isinstance(payload, dict) else {}


def write_mineru_batch(batch_key: str, payload: Dict[str, Any], cache_dir: Optional[str] = None) -> Dict[str, Any]:
    stored = dict(payload)
    stored["batch_key"] = batch_key
    stored["updated_at"] = utc_now()
    write_json(mineru_batch_path(batch_key, cache_dir), stored)
    return stored
