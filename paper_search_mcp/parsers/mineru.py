from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..cache import (
    get_cached_paths,
    index_parsed_paper,
    mineru_batches_root,
    paper_dir,
    paper_key,
    read_json,
    read_mineru_batch,
    record_download,
    resolved_parsed_paths,
    sha256_file,
    utc_now,
    visible_artifact_paths,
    write_json,
    write_mineru_batch,
)
from ..config import get_env


SUPPORTED_MODES = {"auto", "extract", "local_api", "cloud_api", "cli", "pypdf"}


def sha256_file_from_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


@dataclass
class ParseResult:
    paper_key: str
    status: str
    parser: str
    backend: str
    mode: str
    full_md_path: str
    content_list_path: str
    manifest_path: str
    assets_dir: str
    result_zip_path: str
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paper_key": self.paper_key,
            "status": self.status,
            "parser": self.parser,
            "backend": self.backend,
            "mode": self.mode,
            "full_md_path": self.full_md_path,
            "content_list_path": self.content_list_path,
            "manifest_path": self.manifest_path,
            "assets_dir": self.assets_dir,
            "result_zip_path": self.result_zip_path,
            "message": self.message,
        }


class MinerUParser:
    """MinerU adapter using the official extract API by default."""

    def __init__(
        self,
        *,
        mode: str = "auto",
        base_url: str = "",
        api_key: str = "",
        backend: str = "",
        cache_dir: str = "",
        timeout: int = 600,
    ) -> None:
        configured_mode = mode or get_env("MINERU_MODE", "auto")
        configured_mode = configured_mode.strip().lower()
        self.mode = configured_mode if configured_mode in SUPPORTED_MODES else "auto"
        self.base_url = (base_url or get_env("MINERU_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.api_key = api_key or get_env("MINERU_API_KEY", "")
        self.backend = backend or get_env("MINERU_BACKEND", "pipeline")
        self.cache_dir = cache_dir or get_env("CACHE_DIR", "")
        self.timeout = int(get_env("MINERU_TIMEOUT", str(timeout)) or timeout)
        self.extract_base_url = get_env("MINERU_EXTRACT_BASE_URL", "https://mineru.net/api/v4").rstrip("/")
        self.extract_model_version = get_env("MINERU_MODEL_VERSION", "vlm")
        self.extract_language = get_env("MINERU_LANGUAGE", "ch")
        self.extract_poll_interval = float(get_env("MINERU_POLL_INTERVAL", "5") or "5")
        self.extract_is_ocr = self._env_bool("MINERU_IS_OCR", False)
        self.extract_enable_formula = self._env_bool("MINERU_ENABLE_FORMULA", True)
        self.extract_enable_table = self._env_bool("MINERU_ENABLE_TABLE", True)
        self.extract_page_ranges = get_env("MINERU_PAGE_RANGES", "")
        self.export_zip = self._env_bool("MINERU_EXPORT_ZIP", False)
        self.extract_extra_formats = [
            value.strip()
            for value in get_env("MINERU_EXTRA_FORMATS", "").split(",")
            if value.strip()
        ]
        self._ensure_extract_oss_no_proxy()

    def parse_pdf(
        self,
        pdf_path: str,
        *,
        paper_key_hint: str = "",
        source: str = "",
        paper_id: str = "",
        doi: str = "",
        title: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        pdf = Path(pdf_path).expanduser().resolve()
        if not pdf.exists():
            raise FileNotFoundError(f"PDF not found: {pdf}")

        key = paper_key(
            paper_key_hint=paper_key_hint,
            doi=doi,
            source=source,
            paper_id=paper_id,
            title=title,
            pdf_path=str(pdf),
        )
        directory = paper_dir(key, self.cache_dir)
        mineru_dir = directory / "mineru"
        paths = get_cached_paths(key, self.cache_dir)
        visible_paths = visible_artifact_paths(pdf)
        result_zip_path = Path(visible_paths["result_zip"])

        parsed_paths = resolved_parsed_paths(key, self.cache_dir)
        cached_md = Path(parsed_paths["full_md"])
        cached_manifest = Path(parsed_paths["manifest"])
        if not force and cached_md.exists() and cached_manifest.exists():
            result = ParseResult(
                paper_key=key,
                status="cached",
                parser=read_json(cached_manifest, {}).get("parser", "mineru"),
                backend=read_json(cached_manifest, {}).get("backend", self.backend),
                mode=read_json(cached_manifest, {}).get("mode", self.mode),
                full_md_path=visible_paths["full_md"],
                content_list_path=visible_paths["content_list"],
                manifest_path=visible_paths["manifest"],
                assets_dir=visible_paths["assets_dir"],
                result_zip_path=visible_paths["result_zip"],
                message="Using existing parsed cache.",
            )
            result_dict = result.to_dict()
            write_json(directory / "status.json", result_dict)
            self._export_visible_artifacts(key=key, visible_paths=visible_paths, result=result_dict)
            self._attach_index_status(key, visible_paths, result_dict)
            return result_dict

        record_download(
            pdf_path=str(pdf),
            paper_key_hint=key,
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            downloader="parser-input",
            legal_status="user_provided_or_previously_downloaded",
            cache_dir=self.cache_dir,
        )

        mineru_dir.mkdir(parents=True, exist_ok=True)

        errors: List[str] = []
        modes = self._mode_order()
        for mode in modes:
            try:
                if mode == "local_api":
                    payload = self._parse_with_local_api(pdf)
                elif mode == "extract":
                    payload = self._parse_with_extract_api(pdf, upload_name=pdf.name)
                elif mode == "cloud_api":
                    payload = self._parse_with_cloud_api(pdf, upload_name=pdf.name)
                elif mode == "cli":
                    payload = self._parse_with_cli(pdf, mineru_dir)
                elif mode == "pypdf":
                    payload = self._parse_with_pypdf(pdf)
                else:
                    continue

                self._write_artifacts(
                    payload=payload,
                    key=key,
                    pdf_path=pdf,
                    mineru_dir=mineru_dir,
                    mode=mode,
                    result_zip_path=result_zip_path,
                )
                result = ParseResult(
                    paper_key=key,
                    status="ok",
                    parser=payload.get("parser", "mineru" if mode != "pypdf" else "pypdf"),
                    backend=payload.get("backend", self.backend if mode != "pypdf" else "pypdf"),
                    mode=mode,
                    full_md_path=visible_paths["full_md"],
                    content_list_path=visible_paths["content_list"],
                    manifest_path=visible_paths["manifest"],
                    assets_dir=visible_paths["assets_dir"],
                    result_zip_path=visible_paths["result_zip"],
                    message="; ".join(errors),
                )
                result_dict = result.to_dict()
                write_json(directory / "status.json", result_dict)
                self._export_visible_artifacts(key=key, visible_paths=visible_paths, result=result_dict)
                self._attach_index_status(key, visible_paths, result_dict)
                return result_dict
            except Exception as exc:
                errors.append(f"{mode}: {exc}")
                continue

        result = ParseResult(
            paper_key=key,
            status="error",
            parser="mineru",
            backend=self.backend,
            mode=self.mode,
            full_md_path=visible_paths["full_md"],
            content_list_path=visible_paths["content_list"],
            manifest_path=visible_paths["manifest"],
            assets_dir=visible_paths["assets_dir"],
            result_zip_path=visible_paths["result_zip"],
            message=" | ".join(errors) if errors else "No parser mode attempted.",
        )
        result_dict = result.to_dict()
        write_json(directory / "status.json", result_dict)
        self._export_visible_artifacts(key=key, visible_paths=visible_paths, result=result_dict)
        return result_dict

    def parse_pdfs(self, items: List[Any], *, force: bool = False) -> List[Dict[str, Any]]:
        """Parse multiple PDFs, using MinerU extract batch when it is the first viable mode."""
        normalized = self._normalize_parse_items(items)
        if not normalized:
            return []

        if len(normalized) < 2 or not self._batch_extract_enabled():
            return [self.parse_pdf(**item, force=force) for item in normalized]

        cached_results: Dict[int, Dict[str, Any]] = {}
        pending: List[Dict[str, Any]] = []
        for index, item in enumerate(normalized):
            pdf = Path(item["pdf_path"]).expanduser().resolve()
            key = paper_key(
                paper_key_hint=item.get("paper_key_hint", ""),
                doi=item.get("doi", ""),
                source=item.get("source", ""),
                paper_id=item.get("paper_id", ""),
                title=item.get("title", ""),
                pdf_path=str(pdf),
            )
            parsed_paths = resolved_parsed_paths(key, self.cache_dir)
            if not force and Path(parsed_paths["full_md"]).exists() and Path(parsed_paths["manifest"]).exists():
                cached_results[index] = self.parse_pdf(**item, force=False)
                continue

            pending.append({**item, "index": index, "pdf": pdf, "paper_key": key})

        batch_results: Dict[int, Dict[str, Any]] = {}
        if pending:
            batch_key = self._batch_key(pending)
            reusable_payloads, pending_for_extract, batch_manifest = self._load_reusable_batch_payloads(
                batch_key,
                pending,
                force=force,
            )
            try:
                if pending_for_extract:
                    payloads, failures = self._parse_with_extract_api_batch(
                        pending_for_extract,
                        batch_key=batch_key,
                        manifest=batch_manifest,
                    )
                else:
                    payloads, failures = {}, {}
                payloads.update(reusable_payloads)
            except Exception as exc:
                payloads, failures = dict(reusable_payloads), {
                    str(item["paper_key"]): f"batch extract failed: {exc}" for item in pending_for_extract
                }

            for item in pending:
                index = int(item["index"])
                key = str(item["paper_key"])
                payload = payloads.get(key)
                if not payload:
                    batch_results[index] = self.parse_pdf(
                        item["pdf_path"],
                        paper_key_hint=item.get("paper_key_hint", ""),
                        source=item.get("source", ""),
                        paper_id=item.get("paper_id", ""),
                        doi=item.get("doi", ""),
                        title=item.get("title", ""),
                        force=force,
                    )
                    if failures.get(key):
                        message = str(batch_results[index].get("message") or "")
                        prefix = f"extract_batch: {failures[key]}"
                        batch_results[index]["message"] = f"{prefix}; {message}" if message else prefix
                    continue

                pdf = Path(item["pdf"]).expanduser().resolve()
                record_download(
                    pdf_path=str(pdf),
                    paper_key_hint=key,
                    source=item.get("source", ""),
                    paper_id=item.get("paper_id", ""),
                    doi=item.get("doi", ""),
                    title=item.get("title", ""),
                    downloader="parser-input",
                    legal_status="user_provided_or_previously_downloaded",
                    cache_dir=self.cache_dir,
                )
                directory = paper_dir(key, self.cache_dir)
                mineru_dir = directory / "mineru"
                mineru_dir.mkdir(parents=True, exist_ok=True)
                visible_paths = visible_artifact_paths(pdf)
                result_zip_path = Path(visible_paths["result_zip"])
                try:
                    self._write_artifacts(
                        payload=payload,
                        key=key,
                        pdf_path=pdf,
                        mineru_dir=mineru_dir,
                        mode="extract",
                        result_zip_path=result_zip_path,
                    )
                    result = ParseResult(
                        paper_key=key,
                        status="ok",
                        parser=payload.get("parser", "mineru"),
                        backend=payload.get("backend", self.extract_model_version),
                        mode="extract",
                        full_md_path=visible_paths["full_md"],
                        content_list_path=visible_paths["content_list"],
                        manifest_path=visible_paths["manifest"],
                        assets_dir=visible_paths["assets_dir"],
                        result_zip_path=visible_paths["result_zip"],
                    ).to_dict()
                    write_json(directory / "status.json", result)
                    self._export_visible_artifacts(key=key, visible_paths=visible_paths, result=result)
                    self._attach_index_status(key, visible_paths, result)
                    batch_results[index] = result
                except Exception as exc:
                    fallback = self.parse_pdf(
                        item["pdf_path"],
                        paper_key_hint=item.get("paper_key_hint", ""),
                        source=item.get("source", ""),
                        paper_id=item.get("paper_id", ""),
                        doi=item.get("doi", ""),
                        title=item.get("title", ""),
                        force=True,
                    )
                    message = str(fallback.get("message") or "")
                    fallback["message"] = f"extract_batch_write: {exc}; {message}" if message else f"extract_batch_write: {exc}"
                    batch_results[index] = fallback

        return [cached_results.get(index) or batch_results[index] for index in range(len(normalized))]

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "mode": self.mode,
            "base_url": self.base_url,
            "backend": self.backend,
            "auto_order": self._mode_order(),
            "export_zip": self.export_zip,
            "extract_api": {"ok": False, "message": "", "base_url": self.extract_base_url},
            "local_api": {"ok": False, "message": "not checked by default"},
            "cli": {"ok": False, "message": "not checked by default"},
            "pypdf": {"ok": False, "message": ""},
        }

        if self.api_key:
            result["extract_api"] = {
                "ok": True,
                "message": "MINERU_API_KEY configured",
                "base_url": self.extract_base_url,
            }
        else:
            result["extract_api"] = {
                "ok": False,
                "message": "PAPER_SEARCH_MCP_MINERU_API_KEY is not set",
                "base_url": self.extract_base_url,
            }

        mode_order = set(self._mode_order())
        if self.mode == "local_api" or "local_api" in mode_order:
            try:
                response = requests.get(f"{self.base_url}/health", timeout=5)
                result["local_api"] = {"ok": response.status_code < 500, "message": str(response.status_code)}
            except Exception as exc:
                result["local_api"] = {"ok": False, "message": str(exc)}

        if self.mode == "cli" or "cli" in mode_order:
            command = shutil.which("mineru")
            if command:
                result["cli"] = {"ok": True, "message": command}
            else:
                result["cli"] = {"ok": False, "message": "mineru command not found on PATH"}

        try:
            import pypdf  # noqa: F401

            result["pypdf"] = {"ok": True, "message": "pypdf import ok"}
        except Exception as exc:
            result["pypdf"] = {"ok": False, "message": str(exc)}

        return result

    def _mode_order(self) -> List[str]:
        if self.mode == "auto":
            configured = get_env("MINERU_AUTO_ORDER", "").strip()
            if configured:
                modes = [mode.strip().lower() for mode in configured.split(",") if mode.strip()]
            elif self.api_key:
                modes = ["extract", "local_api", "cli", "pypdf"]
            else:
                modes = ["local_api", "cli", "pypdf"]
            deduped: List[str] = []
            for mode in modes:
                if mode in SUPPORTED_MODES and mode != "auto" and mode not in deduped:
                    deduped.append(mode)
            return deduped or ["pypdf"]
        return [self.mode]

    def _batch_extract_enabled(self) -> bool:
        if not self.api_key:
            return False
        order = self._mode_order()
        return bool(order and order[0] in {"extract", "cloud_api"})

    def _batch_key(self, entries: List[Dict[str, Any]]) -> str:
        parts = [
            self.extract_model_version,
            self.extract_language,
            str(self.extract_is_ocr),
            str(self.extract_enable_formula),
            str(self.extract_enable_table),
            self.extract_page_ranges,
        ]
        for entry in sorted(entries, key=lambda item: str(item.get("paper_key") or "")):
            pdf = Path(entry["pdf"]).expanduser().resolve()
            parts.append(f"{entry.get('paper_key')}:{sha256_file(pdf)}")
        return "mineru_batch_" + self._safe_data_id("_".join(parts))[:48] + "_" + sha256_file_from_text("|".join(parts))[:12]

    def _batch_zip_path(self, batch_key: str, paper_key_value: str) -> Path:
        directory = mineru_batches_root(self.cache_dir) / self._safe_data_id(batch_key)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{self._safe_data_id(paper_key_value)}.zip"

    def _load_reusable_batch_payloads(
        self,
        batch_key: str,
        entries: List[Dict[str, Any]],
        *,
        force: bool,
    ) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        manifest = read_mineru_batch(batch_key, self.cache_dir)
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        if force or not isinstance(files, dict):
            return {}, entries, manifest if isinstance(manifest, dict) else {}

        reusable: Dict[str, Dict[str, Any]] = {}
        pending: List[Dict[str, Any]] = []
        for entry in entries:
            key = str(entry["paper_key"])
            record = files.get(key, {})
            zip_path = Path(str(record.get("zip_path") or ""))
            if record.get("status") == "downloaded" and zip_path.exists():
                reusable[key] = {
                    "parser": "mineru",
                    "backend": self.extract_model_version,
                    "mode": "extract",
                    "zip_bytes": zip_path.read_bytes(),
                    "raw": {
                        "batch_key": batch_key,
                        "batch_id": manifest.get("batch_id", ""),
                        "data_id": record.get("data_id", ""),
                        "extract_result": record.get("extract_result", {}),
                        "zip_url": record.get("zip_url", ""),
                        "reused_cached_batch_zip": True,
                    },
                }
            else:
                pending.append(entry)
        return reusable, pending, manifest if isinstance(manifest, dict) else {}

    def _write_batch_manifest(self, batch_key: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        return write_mineru_batch(batch_key, manifest, self.cache_dir)

    @staticmethod
    def _normalize_parse_items(items: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, (str, Path)):
                raw: Dict[str, Any] = {"pdf_path": str(item)}
            elif isinstance(item, dict):
                raw = dict(item)
            else:
                raise TypeError(f"Unsupported parse batch item: {item!r}")

            pdf_path = str(raw.get("pdf_path") or raw.get("path") or "").strip()
            if not pdf_path:
                raise ValueError("Batch parse item is missing pdf_path")
            normalized.append(
                {
                    "pdf_path": pdf_path,
                    "paper_key_hint": str(raw.get("paper_key") or raw.get("paper_key_hint") or ""),
                    "source": str(raw.get("source") or ""),
                    "paper_id": str(raw.get("paper_id") or ""),
                    "doi": str(raw.get("doi") or ""),
                    "title": str(raw.get("title") or ""),
                }
            )
        return normalized

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json, application/zip, */*"}
        if self.api_key:
            headers["Authorization"] = self._authorization_value()
        return headers

    def _authorization_value(self) -> str:
        value = self.api_key.strip()
        if value.lower().startswith("bearer "):
            return value
        return f"Bearer {value}"

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = get_env(name, str(default)).strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            return True
        if value in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def _ensure_extract_oss_no_proxy(self) -> None:
        """Bypass local/system proxies for MinerU's Aliyun OSS upload URLs."""
        if not self._env_bool("MINERU_OSS_NO_PROXY", True):
            return

        hosts = [
            value.strip()
            for value in get_env(
                "MINERU_OSS_NO_PROXY_HOSTS",
                ".aliyuncs.com,mineru.oss-cn-shanghai.aliyuncs.com",
            ).split(",")
            if value.strip()
        ]
        if not hosts:
            return

        merged: List[str] = []
        seen: set[str] = set()
        for key in ("NO_PROXY", "no_proxy"):
            for value in os.environ.get(key, "").split(","):
                value = value.strip()
                if value and value.lower() not in seen:
                    merged.append(value)
                    seen.add(value.lower())
        for host in hosts:
            if host.lower() not in seen:
                merged.append(host)
                seen.add(host.lower())

        value = ",".join(merged)
        os.environ["NO_PROXY"] = value
        os.environ["no_proxy"] = value

    def _parse_with_local_api(self, pdf_path: Path) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/file_parse"
        data = {
            "backend": self.backend,
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "return_md": "true",
            "return_content_list": "true",
            "return_images": "true",
            "response_format_zip": "true",
            "return_original_file": "false",
        }
        with pdf_path.open("rb") as fh:
            files = {"file": (pdf_path.name, fh, "application/pdf")}
            response = requests.post(endpoint, data=data, files=files, headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        return self._decode_response(response, parser="mineru", mode="local_api")

    def _parse_with_cloud_api(self, pdf_path: Path, *, upload_name: str = "") -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("PAPER_SEARCH_MCP_MINERU_API_KEY is required for cloud_api mode")
        return self._parse_with_extract_api(pdf_path, upload_name=upload_name)

    def _parse_with_extract_api(self, pdf_path: Path, *, upload_name: str = "") -> Dict[str, Any]:
        """Use MinerU v4 precise extraction for local PDFs.

        Flow: request signed upload URL, PUT the PDF, poll batch result, then
        download the full result zip.
        """
        if not self.api_key:
            raise ValueError("PAPER_SEARCH_MCP_MINERU_API_KEY is required for extract mode")

        public_name = upload_name or pdf_path.name
        public_stem = Path(public_name).stem or pdf_path.stem
        data_id = self._safe_data_id(f"{public_stem}_{sha256_file(pdf_path)[:12]}")
        file_payload: Dict[str, Any] = {
            "name": public_name,
            "data_id": data_id,
            "is_ocr": self.extract_is_ocr,
        }
        if self.extract_page_ranges:
            file_payload["page_ranges"] = self.extract_page_ranges

        payload: Dict[str, Any] = {
            "files": [file_payload],
            "model_version": self.extract_model_version,
            "enable_formula": self.extract_enable_formula,
            "enable_table": self.extract_enable_table,
            "language": self.extract_language,
        }
        if self.extract_extra_formats:
            payload["extra_formats"] = self.extract_extra_formats

        upload_info = self._post_extract_json("/file-urls/batch", payload)
        batch_id = str(upload_info.get("batch_id") or "").strip()
        file_urls = upload_info.get("file_urls") or []
        if not batch_id or not file_urls:
            raise RuntimeError("MinerU extract did not return batch_id and file_urls")

        upload_url = self._extract_upload_url(file_urls[0])
        with pdf_path.open("rb") as fh:
            upload_response = requests.put(upload_url, data=fh, timeout=self.timeout)
        upload_response.raise_for_status()

        extract_result = self._wait_for_extract_batch(batch_id=batch_id, data_id=data_id)
        zip_url = str(extract_result.get("full_zip_url") or "").strip()
        if not zip_url:
            raise RuntimeError("MinerU extract finished without full_zip_url")

        zip_response = requests.get(zip_url, timeout=self.timeout)
        zip_response.raise_for_status()
        if not zip_response.content.startswith(b"PK"):
            content_type = zip_response.headers.get("content-type", "")
            raise RuntimeError(f"MinerU result URL did not return a zip file (content-type={content_type})")

        return {
            "parser": "mineru",
            "backend": self.extract_model_version,
            "mode": "extract",
            "zip_bytes": zip_response.content,
            "raw": {
                "batch_id": batch_id,
                "data_id": data_id,
                "extract_result": extract_result,
                "zip_url": zip_url,
            },
        }

    def _parse_with_extract_api_batch(
        self,
        entries: List[Dict[str, Any]],
        *,
        batch_key: str = "",
        manifest: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        if not self.api_key:
            raise ValueError("PAPER_SEARCH_MCP_MINERU_API_KEY is required for extract mode")

        batch_entries: List[Dict[str, Any]] = []
        used_data_ids: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            pdf = Path(entry["pdf"]).expanduser().resolve()
            if not pdf.exists():
                raise FileNotFoundError(f"PDF not found: {pdf}")
            upload_name = pdf.name
            public_stem = Path(upload_name).stem or pdf.stem
            data_id = self._safe_data_id(f"{public_stem}_{sha256_file(pdf)[:12]}")
            if data_id in used_data_ids:
                data_id = self._safe_data_id(f"{data_id}_{index}")
            used_data_ids.add(data_id)
            file_payload: Dict[str, Any] = {
                "name": upload_name,
                "data_id": data_id,
                "is_ocr": self.extract_is_ocr,
            }
            if self.extract_page_ranges:
                file_payload["page_ranges"] = self.extract_page_ranges
            batch_entries.append({**entry, "pdf": pdf, "data_id": data_id, "file_payload": file_payload})

        batch_key = batch_key or self._batch_key(batch_entries)
        manifest = dict(manifest or {})
        manifest.update(
            {
                "batch_key": batch_key,
                "status": "submitting",
                "model_version": self.extract_model_version,
                "language": self.extract_language,
                "created_at": manifest.get("created_at") or utc_now(),
                "files": manifest.get("files") if isinstance(manifest.get("files"), dict) else {},
            }
        )
        for entry in batch_entries:
            key = str(entry["paper_key"])
            manifest["files"][key] = {
                **manifest["files"].get(key, {}),
                "paper_key": key,
                "pdf_path": str(entry["pdf"]),
                "pdf_sha256": sha256_file(entry["pdf"]),
                "data_id": entry["data_id"],
                "status": "queued",
            }
        self._write_batch_manifest(batch_key, manifest)

        payload: Dict[str, Any] = {
            "files": [entry["file_payload"] for entry in batch_entries],
            "model_version": self.extract_model_version,
            "enable_formula": self.extract_enable_formula,
            "enable_table": self.extract_enable_table,
            "language": self.extract_language,
        }
        if self.extract_extra_formats:
            payload["extra_formats"] = self.extract_extra_formats

        upload_info = self._post_extract_json("/file-urls/batch", payload)
        batch_id = str(upload_info.get("batch_id") or "").strip()
        file_urls = upload_info.get("file_urls") or []
        if not batch_id or not isinstance(file_urls, list) or len(file_urls) < len(batch_entries):
            raise RuntimeError("MinerU extract did not return enough batch upload URLs")

        manifest["batch_id"] = batch_id
        manifest["status"] = "uploading"
        for entry in batch_entries:
            manifest["files"][str(entry["paper_key"])]["status"] = "uploading"
        self._write_batch_manifest(batch_key, manifest)

        def upload_one(entry: Dict[str, Any], upload_url_value: Any) -> None:
            upload_url = self._extract_upload_url(upload_url_value)
            with Path(entry["pdf"]).open("rb") as fh:
                response = requests.put(upload_url, data=fh, timeout=self.timeout)
            response.raise_for_status()

        upload_workers = self._batch_worker_count("MINERU_UPLOAD_CONCURRENCY", len(batch_entries))
        with ThreadPoolExecutor(max_workers=upload_workers) as executor:
            futures = [
                executor.submit(upload_one, entry, file_urls[index])
                for index, entry in enumerate(batch_entries)
            ]
            for future in as_completed(futures):
                future.result()

        manifest["status"] = "polling"
        for entry in batch_entries:
            file_record = manifest["files"][str(entry["paper_key"])]
            file_record["status"] = "uploaded"
        self._write_batch_manifest(batch_key, manifest)

        done, failures = self._wait_for_extract_batch_all(
            batch_id=batch_id,
            data_ids=[entry["data_id"] for entry in batch_entries],
        )

        payloads: Dict[str, Dict[str, Any]] = {}
        data_id_to_entry = {str(entry["data_id"]): entry for entry in batch_entries}
        for data_id, result in done.items():
            entry = data_id_to_entry.get(str(data_id))
            if not entry:
                continue
            file_record = manifest["files"][str(entry["paper_key"])]
            file_record["status"] = "done"
            file_record["extract_result"] = result
            file_record["zip_url"] = str(result.get("full_zip_url") or "")
        for data_id, message in failures.items():
            entry = data_id_to_entry.get(str(data_id))
            if not entry:
                continue
            file_record = manifest["files"][str(entry["paper_key"])]
            file_record["status"] = "failed"
            file_record["error"] = message
        self._write_batch_manifest(batch_key, manifest)

        def download_zip(entry: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
            key = str(entry["paper_key"])
            data_id = str(entry["data_id"])
            extract_result = done[data_id]
            zip_url = str(extract_result.get("full_zip_url") or "").strip()
            if not zip_url:
                raise RuntimeError("MinerU extract finished without full_zip_url")
            zip_response = requests.get(zip_url, timeout=self.timeout)
            zip_response.raise_for_status()
            if not zip_response.content.startswith(b"PK"):
                content_type = zip_response.headers.get("content-type", "")
                raise RuntimeError(f"MinerU result URL did not return a zip file (content-type={content_type})")
            zip_path = self._batch_zip_path(batch_key, key)
            zip_path.write_bytes(zip_response.content)
            return key, {
                "parser": "mineru",
                "backend": self.extract_model_version,
                "mode": "extract",
                "zip_bytes": zip_response.content,
                "raw": {
                    "batch_key": batch_key,
                    "batch_id": batch_id,
                    "data_id": data_id,
                    "extract_result": extract_result,
                    "zip_url": zip_url,
                    "zip_path": str(zip_path),
                    "batch_size": len(batch_entries),
                },
            }

        download_entries = [entry for entry in batch_entries if entry["data_id"] in done]
        download_workers = self._batch_worker_count("MINERU_DOWNLOAD_CONCURRENCY", len(download_entries))
        with ThreadPoolExecutor(max_workers=download_workers) as executor:
            future_map = {executor.submit(download_zip, entry): entry for entry in download_entries}
            for future in as_completed(future_map):
                entry = future_map[future]
                try:
                    key, result_payload = future.result()
                    payloads[key] = result_payload
                    file_record = manifest["files"][key]
                    file_record["status"] = "downloaded"
                    file_record["zip_path"] = result_payload["raw"].get("zip_path", "")
                except Exception as exc:
                    failures[str(entry["paper_key"])] = str(exc)
                    file_record = manifest["files"][str(entry["paper_key"])]
                    file_record["status"] = "failed"
                    file_record["error"] = str(exc)

        data_id_to_key = {str(entry["data_id"]): str(entry["paper_key"]) for entry in batch_entries}
        keyed_failures = {
            data_id_to_key.get(str(data_id), str(data_id)): message
            for data_id, message in failures.items()
        }
        manifest["status"] = "ok" if not keyed_failures else "partial" if payloads else "failed"
        manifest["failures"] = keyed_failures
        self._write_batch_manifest(batch_key, manifest)
        return payloads, keyed_failures

    def _post_extract_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = f"{self.extract_base_url}{path}"
        response = requests.post(endpoint, headers=self._headers(), json=payload, timeout=self.timeout)
        response.raise_for_status()
        return self._extract_data(response.json(), endpoint)

    def _get_extract_json(self, path: str) -> Dict[str, Any]:
        endpoint = f"{self.extract_base_url}{path}"
        response = requests.get(endpoint, headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        return self._extract_data(response.json(), endpoint)

    @staticmethod
    def _extract_data(payload: Dict[str, Any], endpoint: str) -> Dict[str, Any]:
        code = payload.get("code", 0)
        if code not in {0, "0", None}:
            raise RuntimeError(f"MinerU API error at {endpoint}: {payload.get('msg') or payload}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"MinerU API response missing data at {endpoint}: {payload}")
        return data

    @staticmethod
    def _extract_upload_url(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("url", "file_url", "upload_url", "uploadUrl", "uploadURL"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
        raise RuntimeError("MinerU extract upload URL is missing")

    @staticmethod
    def _safe_data_id(value: str) -> str:
        safe = "".join(char if char.isalnum() or char in "_.-" else "_" for char in value)
        return (safe.strip("._-") or "paper")[:128]

    def _wait_for_extract_batch(self, *, batch_id: str, data_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.timeout
        last_state = ""
        while time.time() < deadline:
            data = self._get_extract_json(f"/extract-results/batch/{batch_id}")
            results = data.get("extract_result") or data.get("extract_results") or data.get("results") or []
            if not isinstance(results, list):
                raise RuntimeError(f"MinerU batch result is not a list: {data}")

            selected = self._select_extract_result(results, data_id)
            state = str(selected.get("state") or "").lower()
            last_state = state or last_state
            if state in {"done", "completed", "success", "succeeded"}:
                return selected
            if state in {"failed", "error"}:
                raise RuntimeError(selected.get("err_msg") or "MinerU extract task failed")

            time.sleep(max(1.0, self.extract_poll_interval))

        raise TimeoutError(f"MinerU extract batch {batch_id} timed out; last_state={last_state or 'unknown'}")

    def _wait_for_extract_batch_all(
        self,
        *,
        batch_id: str,
        data_ids: List[str],
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        deadline = time.time() + self.timeout
        pending = set(data_ids)
        done: Dict[str, Dict[str, Any]] = {}
        failures: Dict[str, str] = {}
        last_state = ""

        while pending and time.time() < deadline:
            data = self._get_extract_json(f"/extract-results/batch/{batch_id}")
            results = data.get("extract_result") or data.get("extract_results") or data.get("results") or []
            if not isinstance(results, list):
                raise RuntimeError(f"MinerU batch result is not a list: {data}")

            by_id = {
                str(item.get("data_id") or ""): item
                for item in results
                if isinstance(item, dict) and item.get("data_id")
            }
            for data_id in list(pending):
                selected = by_id.get(data_id)
                if not selected:
                    continue
                state = str(selected.get("state") or "").lower()
                last_state = state or last_state
                if state in {"done", "completed", "success", "succeeded"}:
                    done[data_id] = selected
                    pending.remove(data_id)
                elif state in {"failed", "error"}:
                    failures[data_id] = str(selected.get("err_msg") or "MinerU extract task failed")
                    pending.remove(data_id)

            if pending:
                time.sleep(max(1.0, self.extract_poll_interval))

        if pending:
            for data_id in pending:
                failures[data_id] = f"MinerU extract batch {batch_id} timed out; last_state={last_state or 'unknown'}"
        return done, failures

    @staticmethod
    def _batch_worker_count(env_name: str, item_count: int) -> int:
        if item_count <= 0:
            return 1
        raw = get_env(env_name, "").strip()
        try:
            configured = int(raw) if raw else min(4, item_count)
        except ValueError:
            configured = min(4, item_count)
        return max(1, min(configured, item_count))

    @staticmethod
    def _select_extract_result(results: List[Dict[str, Any]], data_id: str) -> Dict[str, Any]:
        for item in results:
            if isinstance(item, dict) and str(item.get("data_id") or "") == data_id:
                return item
        for item in results:
            if isinstance(item, dict):
                return item
        raise RuntimeError("MinerU extract batch did not return any file result")

    def _parse_with_cli(self, pdf_path: Path, mineru_dir: Path) -> Dict[str, Any]:
        command = shutil.which("mineru")
        if not command:
            raise FileNotFoundError("mineru command not found on PATH")

        raw_dir = Path(tempfile.mkdtemp(prefix="mineru_cli_"))
        cmd = [command, "-p", str(pdf_path), "-o", str(raw_dir)]
        if self.backend:
            cmd.extend(["-b", self.backend])
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "mineru CLI failed").strip())

            payload = self._collect_files_from_dir(raw_dir, parser="mineru", mode="cli")
            payload["raw_dir_temporary"] = True
            return payload
        except Exception:
            shutil.rmtree(raw_dir, ignore_errors=True)
            raise

    def _parse_with_pypdf(self, pdf_path: Path) -> Dict[str, Any]:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        blocks: List[Dict[str, Any]] = []
        md_parts: List[str] = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            if not text:
                continue
            heading = f"## Page {index}"
            md_parts.extend([heading, "", text, ""])
            blocks.append(
                {
                    "id": f"page-{index}",
                    "type": "page_text",
                    "page": index,
                    "text": text,
                    "markdown": f"{heading}\n\n{text}",
                    "asset_paths": [],
                    "section_path": [heading],
                    "order": len(blocks),
                }
            )

        markdown = "\n".join(md_parts).strip()
        if not markdown:
            markdown = "No extractable text found in PDF."
        return {
            "parser": "pypdf",
            "backend": "pypdf",
            "mode": "pypdf",
            "markdown": markdown,
            "content_list": blocks,
            "assets": [],
            "raw": {},
        }

    def _decode_response(self, response: requests.Response, *, parser: str, mode: str) -> Dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        if "zip" in content_type or response.content.startswith(b"PK"):
            return {"parser": parser, "mode": mode, "backend": self.backend, "zip_bytes": response.content}

        try:
            data = response.json()
        except Exception:
            text = response.text.strip()
            return {"parser": parser, "mode": mode, "backend": self.backend, "markdown": text, "content_list": []}

        markdown = self._first_value(data, ["markdown", "md", "full_md", "fullMarkdown", "content"])
        content_list = self._first_value(data, ["content_list", "contentList", "blocks", "items"]) or []
        return {
            "parser": parser,
            "mode": mode,
            "backend": data.get("backend", self.backend),
            "markdown": markdown or "",
            "content_list": content_list,
            "raw": data,
        }

    @staticmethod
    def _first_value(data: Dict[str, Any], names: List[str]) -> Any:
        for name in names:
            if name in data:
                return data[name]
        nested = data.get("data")
        if isinstance(nested, dict):
            for name in names:
                if name in nested:
                    return nested[name]
        return None

    def _collect_files_from_dir(self, raw_dir: Path, *, parser: str, mode: str) -> Dict[str, Any]:
        markdown = ""
        content_list: Any = []
        manifest: Dict[str, Any] = {}

        md_candidates = list(raw_dir.rglob("full.md")) + list(raw_dir.rglob("*.md"))
        if md_candidates:
            markdown = md_candidates[0].read_text(encoding="utf-8", errors="ignore")

        json_candidates = list(raw_dir.rglob("content_list.json"))
        if not json_candidates:
            json_candidates = list(raw_dir.rglob("*_content_list.json"))
        if json_candidates:
            content_list = read_json(json_candidates[0], [])

        manifest_candidates = list(raw_dir.rglob("manifest.json"))
        if manifest_candidates:
            manifest = read_json(manifest_candidates[0], {}) or {}

        return {
            "parser": parser,
            "mode": mode,
            "backend": manifest.get("backend", self.backend),
            "markdown": markdown,
            "content_list": content_list,
            "raw_dir": str(raw_dir),
            "raw": manifest,
        }

    def _write_artifacts(
        self,
        *,
        payload: Dict[str, Any],
        key: str,
        pdf_path: Path,
        mineru_dir: Path,
        mode: str,
        result_zip_path: Path,
    ) -> None:
        paths = get_cached_paths(key, self.cache_dir)
        visible_paths = visible_artifact_paths(pdf_path)
        assets_dir = Path(visible_paths["assets_dir"])
        assets_dir.mkdir(parents=True, exist_ok=True)
        stale_raw_dir = mineru_dir / "raw"
        if stale_raw_dir.exists():
            shutil.rmtree(stale_raw_dir)

        if payload.get("zip_bytes"):
            # The MinerU result zip is an intermediate transport artifact. Keep
            # the normalized outputs only, so cache/export directories do not
            # grow a redundant mineru/raw folder.
            with tempfile.TemporaryDirectory(prefix="mineru_extract_") as tmp:
                extracted = Path(tmp) / "zip"
                extracted.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(BytesIO(payload["zip_bytes"])) as archive:
                    self._safe_extract_zip(archive, extracted)
                api_raw = payload.get("raw", {})
                collected = self._collect_files_from_dir(extracted, parser="mineru", mode=mode)
                self._copy_assets_from_raw(extracted, assets_dir)
                collected.pop("raw_dir", None)
                payload = {
                    **payload,
                    **collected,
                    "raw": {
                        "api": api_raw,
                        "archive": collected.get("raw", {}),
                    },
                }

        raw_dir_value = payload.get("raw_dir")
        if raw_dir_value:
            raw_dir_path = Path(raw_dir_value)
            try:
                self._copy_assets_from_raw(raw_dir_path, assets_dir)
            finally:
                if payload.get("raw_dir_temporary"):
                    shutil.rmtree(raw_dir_path, ignore_errors=True)

        markdown = payload.get("markdown") or self._markdown_from_content_list(payload.get("content_list", []))
        markdown = self._clean_text(markdown)
        content_list = self._normalize_content_list(payload.get("content_list") or [], markdown)

        Path(visible_paths["full_md"]).write_text(markdown or "", encoding="utf-8")
        write_json(visible_paths["content_list"], content_list)

        manifest = {
            "paper_key": key,
            "parser": payload.get("parser", "mineru"),
            "mode": mode,
            "backend": payload.get("backend", self.backend),
            "created_at": utc_now(),
            "source_pdf": str(pdf_path),
            "pdf_sha256": sha256_file(pdf_path),
            "full_md_path": visible_paths["full_md"],
            "content_list_path": visible_paths["content_list"],
            "assets_dir": visible_paths["assets_dir"],
            "result_zip_path": str(result_zip_path),
            "cache_manifest_path": paths["manifest"],
            "raw": payload.get("raw", {}),
        }
        write_json(visible_paths["manifest"], manifest)
        cache_manifest = dict(manifest)
        cache_manifest.update(
            {
                "visible_full_md_path": visible_paths["full_md"],
                "visible_content_list_path": visible_paths["content_list"],
                "visible_manifest_path": visible_paths["manifest"],
                "visible_assets_dir": visible_paths["assets_dir"],
            }
        )
        write_json(paths["manifest"], cache_manifest)

    def _export_visible_artifacts(self, *, key: str, visible_paths: Dict[str, str], result: Dict[str, Any]) -> Path:
        paths = get_cached_paths(key, self.cache_dir)
        parsed_paths = resolved_parsed_paths(key, self.cache_dir)
        export_dir = Path(visible_paths["export_dir"])
        export_dir.mkdir(parents=True, exist_ok=True)
        parse_succeeded = result.get("status") in {"ok", "cached"}

        if not parse_succeeded:
            for stale_file in ("full_md", "content_list", "manifest"):
                target = Path(visible_paths[stale_file])
                if target.exists():
                    target.unlink()
            stale_assets = Path(visible_paths["assets_dir"])
            if stale_assets.exists():
                shutil.rmtree(stale_assets)

        file_entries = [(Path(paths["metadata"]), Path(visible_paths["metadata"]))]
        if parse_succeeded:
            file_entries.extend(
                [
                    (Path(parsed_paths["full_md"]), Path(visible_paths["full_md"])),
                    (Path(parsed_paths["content_list"]), Path(visible_paths["content_list"])),
                    (Path(parsed_paths["manifest"]), Path(visible_paths["manifest"])),
                ]
            )
        for source, target in file_entries:
            if source.exists() and source.is_file() and source.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        parsed_assets = Path(parsed_paths["assets_dir"])
        visible_assets = Path(visible_paths["assets_dir"])
        if (
            parse_succeeded
            and parsed_assets.exists()
            and parsed_assets.is_dir()
            and parsed_assets.resolve() != visible_assets.resolve()
        ):
            if visible_assets.exists():
                shutil.rmtree(visible_assets)
            shutil.copytree(parsed_assets, visible_assets)

        visible_result = dict(result)
        visible_result.update(
            {
                "full_md_path": visible_paths["full_md"],
                "content_list_path": visible_paths["content_list"],
                "manifest_path": visible_paths["manifest"],
                "assets_dir": visible_paths["assets_dir"],
                "result_zip_path": visible_paths["result_zip"],
            }
        )
        write_json(visible_paths["status"], visible_result)
        write_json(paths["status"], visible_result)

        output_zip = Path(visible_paths["result_zip"]).expanduser().resolve()
        if not self.export_zip:
            if output_zip.exists():
                output_zip.unlink()
            return output_zip

        return self._export_result_zip_from_visible(visible_paths)

    def _attach_index_status(self, key: str, visible_paths: Dict[str, str], result: Dict[str, Any]) -> None:
        try:
            result["index"] = index_parsed_paper(key, self.cache_dir)
        except Exception as exc:
            result["index"] = {"status": "error", "message": str(exc)}
        paths = get_cached_paths(key, self.cache_dir)
        write_json(paths["status"], result)
        write_json(visible_paths["status"], result)

    @staticmethod
    def _export_result_zip_from_visible(visible_paths: Dict[str, str]) -> Path:
        output_zip = Path(visible_paths["result_zip"]).expanduser().resolve()
        output_zip.parent.mkdir(parents=True, exist_ok=True)

        file_entries = [
            ("full.md", Path(visible_paths["full_md"])),
            ("content_list.json", Path(visible_paths["content_list"])),
            ("manifest.json", Path(visible_paths["manifest"])),
            ("metadata.json", Path(visible_paths["metadata"])),
            ("status.json", Path(visible_paths["status"])),
        ]
        directory_entries = [("assets", Path(visible_paths["assets_dir"]))]

        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for arcname, path in file_entries:
                if path.exists() and path.is_file():
                    archive.write(path, arcname)

            for prefix, directory in directory_entries:
                if not directory.exists():
                    continue
                for path in sorted(directory.rglob("*")):
                    if not path.is_file():
                        continue
                    if path.resolve() == output_zip:
                        continue
                    relative = path.relative_to(directory).as_posix()
                    archive.write(path, f"{prefix}/{relative}")

        return output_zip

    @staticmethod
    def _safe_extract_zip(archive: zipfile.ZipFile, target_dir: Path) -> None:
        target_root = target_dir.resolve()
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            if target_root not in destination.parents and destination != target_root:
                raise ValueError(f"Unsafe path in MinerU zip: {member.filename}")
            archive.extract(member, target_dir)

    @staticmethod
    def _copy_assets_from_raw(raw_dir: Path, assets_dir: Path) -> None:
        extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".html", ".csv"}
        for path in raw_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            lower = path.name.lower()
            if "table" in lower or path.suffix.lower() in {".html", ".csv"}:
                bucket = "tables"
            elif "formula" in lower or "equation" in lower:
                bucket = "formulas"
            else:
                bucket = "figures"
            dest_dir = assets_dir / bucket
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / path.name
            if path.resolve() != target.resolve():
                shutil.copy2(path, target)

    @staticmethod
    def _markdown_from_content_list(content_list: Any) -> str:
        if not isinstance(content_list, list):
            return ""
        parts: List[str] = []
        for item in content_list:
            if not isinstance(item, dict):
                continue
            text = item.get("markdown") or item.get("text") or item.get("content") or ""
            if text:
                parts.append(str(text))
        return "\n\n".join(parts)

    @classmethod
    def _clean_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._clean_text(value)
        if isinstance(value, list):
            return [cls._clean_value(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._clean_value(item) for key, item in value.items()}
        return value

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = "" if value is None else str(value)
        return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")

    @staticmethod
    def _normalize_content_list(content_list: Any, markdown: str) -> List[Dict[str, Any]]:
        if isinstance(content_list, list) and content_list:
            normalized: List[Dict[str, Any]] = []
            for index, item in enumerate(content_list):
                if isinstance(item, dict):
                    block = dict(item)
                else:
                    block = {"text": str(item)}
                block = MinerUParser._clean_value(block)
                block.setdefault("id", f"block-{index + 1}")
                block.setdefault("type", block.get("category") or block.get("block_type") or "text")
                block.setdefault("order", index)
                block.setdefault("asset_paths", [])
                normalized.append(block)
            return normalized

        if not markdown:
            return []
        blocks = []
        for index, part in enumerate([p.strip() for p in markdown.split("\n\n") if p.strip()]):
            blocks.append(
                {
                    "id": f"block-{index + 1}",
                    "type": "text",
                    "text": part,
                    "markdown": part,
                    "order": index,
                    "asset_paths": [],
                }
            )
        return blocks


def parse_pdf_with_mineru(
    pdf_path: str,
    *,
    paper_key_hint: str = "",
    source: str = "",
    paper_id: str = "",
    doi: str = "",
    title: str = "",
    mode: str = "auto",
    backend: str = "",
    cache_dir: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    parser = MinerUParser(mode=mode, backend=backend, cache_dir=cache_dir)
    return parser.parse_pdf(
        pdf_path,
        paper_key_hint=paper_key_hint,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        force=force,
    )


def parse_pdfs_with_mineru(
    items: List[Any],
    *,
    mode: str = "auto",
    backend: str = "",
    cache_dir: str = "",
    force: bool = False,
) -> List[Dict[str, Any]]:
    parser = MinerUParser(mode=mode, backend=backend, cache_dir=cache_dir)
    return parser.parse_pdfs(items, force=force)


def mineru_health_check(mode: str = "auto", backend: str = "", cache_dir: str = "") -> Dict[str, Any]:
    parser = MinerUParser(mode=mode, backend=backend, cache_dir=cache_dir)
    return parser.health_check()
