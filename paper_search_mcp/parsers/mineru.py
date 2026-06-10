from __future__ import annotations

import json
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..cache import (
    copy_pdf_to_cache,
    get_cached_paths,
    paper_dir,
    paper_key,
    read_json,
    record_download,
    sha256_file,
    utc_now,
    write_json,
)
from ..config import get_env


SUPPORTED_MODES = {"auto", "extract", "local_api", "cloud_api", "cli", "pypdf"}


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
    """MinerU adapter with a pypdf fallback for environments without MinerU."""

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
        self.extract_extra_formats = [
            value.strip()
            for value in get_env("MINERU_EXTRA_FORMATS", "").split(",")
            if value.strip()
        ]

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
        result_zip_path = pdf.with_suffix(".zip")

        cached_md = Path(paths["full_md"])
        cached_manifest = Path(paths["manifest"])
        if not force and cached_md.exists() and cached_manifest.exists():
            exported_zip = self._export_result_zip(key, result_zip_path)
            result = ParseResult(
                paper_key=key,
                status="cached",
                parser=read_json(cached_manifest, {}).get("parser", "mineru"),
                backend=read_json(cached_manifest, {}).get("backend", self.backend),
                mode=read_json(cached_manifest, {}).get("mode", self.mode),
                full_md_path=paths["full_md"],
                content_list_path=paths["content_list"],
                manifest_path=paths["manifest"],
                assets_dir=paths["assets_dir"],
                result_zip_path=str(exported_zip),
                message="Using existing parsed cache.",
            )
            write_json(directory / "status.json", result.to_dict())
            return result.to_dict()

        cached_pdf = copy_pdf_to_cache(pdf, key, self.cache_dir)
        record_download(
            pdf_path=str(cached_pdf),
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
        (mineru_dir / "assets").mkdir(parents=True, exist_ok=True)

        errors: List[str] = []
        modes = self._mode_order()
        for mode in modes:
            try:
                if mode == "local_api":
                    payload = self._parse_with_local_api(cached_pdf)
                elif mode == "extract":
                    payload = self._parse_with_extract_api(cached_pdf, upload_name=pdf.name)
                elif mode == "cloud_api":
                    payload = self._parse_with_cloud_api(cached_pdf, upload_name=pdf.name)
                elif mode == "cli":
                    payload = self._parse_with_cli(cached_pdf, mineru_dir)
                elif mode == "pypdf":
                    payload = self._parse_with_pypdf(cached_pdf)
                else:
                    continue

                self._write_artifacts(
                    payload=payload,
                    key=key,
                    pdf_path=cached_pdf,
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
                    full_md_path=paths["full_md"],
                    content_list_path=paths["content_list"],
                    manifest_path=paths["manifest"],
                    assets_dir=paths["assets_dir"],
                    result_zip_path=str(result_zip_path),
                    message="; ".join(errors),
                )
                write_json(directory / "status.json", result.to_dict())
                self._export_result_zip(key, result_zip_path)
                return result.to_dict()
            except Exception as exc:
                errors.append(f"{mode}: {exc}")
                continue

        result = ParseResult(
            paper_key=key,
            status="error",
            parser="mineru",
            backend=self.backend,
            mode=self.mode,
            full_md_path=paths["full_md"],
            content_list_path=paths["content_list"],
            manifest_path=paths["manifest"],
            assets_dir=paths["assets_dir"],
            result_zip_path=str(result_zip_path),
            message=" | ".join(errors) if errors else "No parser mode attempted.",
        )
        write_json(directory / "status.json", result.to_dict())
        return result.to_dict()

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "mode": self.mode,
            "base_url": self.base_url,
            "backend": self.backend,
            "extract_api": {"ok": False, "message": "", "base_url": self.extract_base_url},
            "local_api": {"ok": False, "message": ""},
            "cli": {"ok": False, "message": ""},
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

        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            result["local_api"] = {"ok": response.status_code < 500, "message": str(response.status_code)}
        except Exception as exc:
            result["local_api"] = {"ok": False, "message": str(exc)}

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
            if self.api_key:
                return ["extract", "local_api", "cli", "pypdf"]
            return ["local_api", "cli", "pypdf"]
        return [self.mode]

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

        raw_dir = mineru_dir / "raw_cli"
        raw_dir.mkdir(parents=True, exist_ok=True)
        cmd = [command, "-p", str(pdf_path), "-o", str(raw_dir)]
        if self.backend:
            cmd.extend(["-b", self.backend])
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "mineru CLI failed").strip())

        return self._collect_files_from_dir(raw_dir, parser="mineru", mode="cli")

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
        assets_dir = Path(paths["assets_dir"])
        assets_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = mineru_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        if payload.get("zip_bytes"):
            zip_path = raw_dir / "mineru_result.zip"
            zip_path.write_bytes(payload["zip_bytes"])
            extracted = raw_dir / "zip"
            if extracted.exists():
                shutil.rmtree(extracted)
            extracted.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as archive:
                self._safe_extract_zip(archive, extracted)
            api_raw = payload.get("raw", {})
            collected = self._collect_files_from_dir(extracted, parser="mineru", mode=mode)
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
            self._copy_assets_from_raw(Path(raw_dir_value), assets_dir)

        markdown = payload.get("markdown") or self._markdown_from_content_list(payload.get("content_list", []))
        markdown = self._clean_text(markdown)
        content_list = self._normalize_content_list(payload.get("content_list") or [], markdown)

        Path(paths["full_md"]).write_text(markdown or "", encoding="utf-8")
        write_json(paths["content_list"], content_list)

        manifest = {
            "paper_key": key,
            "parser": payload.get("parser", "mineru"),
            "mode": mode,
            "backend": payload.get("backend", self.backend),
            "created_at": utc_now(),
            "source_pdf": str(pdf_path),
            "pdf_sha256": sha256_file(pdf_path),
            "full_md_path": paths["full_md"],
            "content_list_path": paths["content_list"],
            "assets_dir": paths["assets_dir"],
            "result_zip_path": str(result_zip_path),
            "raw": payload.get("raw", {}),
        }
        write_json(paths["manifest"], manifest)

    def _export_result_zip(self, key: str, output_zip: Path) -> Path:
        paths = get_cached_paths(key, self.cache_dir)
        output_zip = output_zip.expanduser().resolve()
        output_zip.parent.mkdir(parents=True, exist_ok=True)

        manifest_path = Path(paths["manifest"])
        manifest = read_json(manifest_path, {}) or {}
        if isinstance(manifest, dict):
            manifest["result_zip_path"] = str(output_zip)
            write_json(manifest_path, manifest)

        file_entries = [
            ("full.md", Path(paths["full_md"])),
            ("content_list.json", Path(paths["content_list"])),
            ("manifest.json", Path(paths["manifest"])),
            ("metadata.json", Path(paths["metadata"])),
            ("status.json", Path(paths["status"])),
        ]
        directory_entries = [
            ("assets", Path(paths["assets_dir"])),
            ("raw", Path(paths["mineru_dir"]) / "raw"),
            ("raw_cli", Path(paths["mineru_dir"]) / "raw_cli"),
        ]

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


def mineru_health_check(mode: str = "auto", backend: str = "", cache_dir: str = "") -> Dict[str, Any]:
    parser = MinerUParser(mode=mode, backend=backend, cache_dir=cache_dir)
    return parser.health_check()
