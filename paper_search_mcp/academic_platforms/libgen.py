"""Opt-in Library Genesis PDF downloader."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from ..config import get_env

logger = logging.getLogger(__name__)

LIBGEN_BASE_URL_ENV = "LIBGEN_BASE_URL"


class LibGenFetcher:
    """Best-effort LibGen JSON API downloader.

    This fetcher is intentionally not wired into the default path. Callers must
    opt in, just as they do for Sci-Hub.
    """

    MIRRORS = (
        "https://libgen.is",
        "https://libgen.st",
        "https://libgen.li",
        "https://libgen.gs",
    )

    def __init__(self, base_url: str = "", output_dir: str = "./downloads", timeout: float = 30.0):
        configured = (base_url or get_env(LIBGEN_BASE_URL_ENV, "")).strip()
        self.base_url = (configured or self.MIRRORS[0]).rstrip("/")
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/json,text/html,application/xhtml+xml,application/pdf,*/*",
            }
        )

    def download_pdf(self, identifier: str) -> Optional[str]:
        """Download a PDF by DOI, title, or LibGen MD5 and return a local path."""
        query = (identifier or "").strip()
        if not query:
            return None

        for base_url in self._candidate_mirrors():
            records = self._search(base_url, query)
            for record in records:
                if str(record.get("extension") or "").strip().lower() != "pdf":
                    continue
                md5 = str(record.get("md5") or "").strip().lower()
                if not md5:
                    continue
                path = self._download_record(base_url, record)
                if path:
                    return path
        return None

    def _candidate_mirrors(self) -> list[str]:
        mirrors = [self.base_url]
        mirrors.extend(url for url in self.MIRRORS if url.rstrip("/") != self.base_url)
        return list(dict.fromkeys(url.rstrip("/") for url in mirrors if url))

    def _search(self, base_url: str, query: str) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "fields": "*",
            "limit1": "10",
        }
        if self._looks_like_doi(query):
            params["doi"] = query
        else:
            params["title"] = query
        try:
            response = self.session.get(
                f"{base_url}/json.php",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.debug("LibGen search failed via %s: %s", base_url, exc)
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    def _download_record(self, base_url: str, record: dict[str, Any]) -> Optional[str]:
        md5 = str(record.get("md5") or "").strip().lower()
        if not md5:
            return None
        urls = [
            f"https://library.lol/main/{md5}",
            f"{base_url}/book/index.php?md5={quote_plus(md5)}",
        ]
        for landing_url in urls:
            download_url = self._extract_download_url(landing_url)
            if not download_url:
                continue
            path = self._stream_pdf(download_url, record)
            if path:
                return path
        return None

    def _extract_download_url(self, landing_url: str) -> str:
        try:
            response = self.session.get(landing_url, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            logger.debug("LibGen landing page failed for %s: %s", landing_url, exc)
            return ""

        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.content[:4096].lstrip().startswith(b"%PDF"):
            return response.url

        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.find_all("a"):
            label = link.get_text(" ", strip=True).lower()
            href = str(link.get("href") or "").strip()
            if not href:
                continue
            if label in {"get", "download", "cloudflare", "ipfs.io"} or href.lower().endswith(".pdf"):
                return urljoin(response.url, href)
        return ""

    def _stream_pdf(self, url: str, record: dict[str, Any]) -> Optional[str]:
        try:
            response = self.session.get(url, stream=True, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            logger.debug("LibGen PDF download failed for %s: %s", url, exc)
            return None

        first = b""
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                if not first:
                    first = chunk[:4096]
                chunks.append(chunk)
                total += len(chunk)
        except Exception as exc:
            logger.debug("LibGen stream failed for %s: %s", url, exc)
            return None
        if total <= 0 or not first.lstrip().startswith(b"%PDF"):
            return None

        filename = self._filename_for_record(record, b"".join(chunks[:2]))
        output_path = self.output_dir / filename
        with output_path.open("wb") as handle:
            for chunk in chunks:
                handle.write(chunk)
        return str(output_path)

    def _filename_for_record(self, record: dict[str, Any], seed: bytes) -> str:
        title = str(record.get("title") or record.get("md5") or "libgen").strip()
        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._-")[:80] or "libgen"
        digest = hashlib.sha256(seed or os.urandom(8)).hexdigest()[:10]
        return f"libgen_{digest}_{safe_title}.pdf"

    @staticmethod
    def _looks_like_doi(value: str) -> bool:
        return bool(re.search(r"10\.\d{4,9}/\S+", value, re.IGNORECASE))
