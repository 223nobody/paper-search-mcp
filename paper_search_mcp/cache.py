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


def copy_pdf_to_cache(pdf_path: str | Path, key: str, cache_dir: Optional[str] = None) -> Path:
    source = Path(pdf_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"PDF not found: {source}")
    destination = paper_dir(key, cache_dir) / "source.pdf"
    if source != destination:
        shutil.copy2(source, destination)
    return destination


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
        full_md = directory / "mineru" / "full.md"
        content_list = directory / "mineru" / "content_list.json"
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
    paths = get_cached_paths(key, cache_dir)
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
    paths = get_cached_paths(key, cache_dir)
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
