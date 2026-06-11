from __future__ import annotations

import hashlib
import json
import re
import shutil
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
