import tempfile
import unittest
import zipfile
import shutil
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from paper_search_mcp.cache import get_cached_paths, read_json, read_parsed
from paper_search_mcp.parsers.mineru import MinerUParser


def _make_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as fh:
        writer.write(fh)


class TestMinerUParser(unittest.TestCase):
    @staticmethod
    def _zip_bytes(entries: dict[str, bytes]) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_pypdf_fallback_writes_standard_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)

            parser = MinerUParser(mode="pypdf", cache_dir=tmp)
            result = parser.parse_pdf(
                str(pdf),
                source="arxiv",
                paper_id="1234.5678",
                title="Fallback Test",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parser"], "pypdf")
            self.assertTrue(Path(result["full_md_path"]).exists())
            self.assertTrue(Path(result["content_list_path"]).exists())
            self.assertTrue(Path(result["manifest_path"]).exists())
            self.assertEqual(Path(result["full_md_path"]).parent, pdf.with_name("paper_mineru"))
            self.assertEqual(Path(result["assets_dir"]), pdf.with_name("paper_mineru") / "assets")
            self.assertEqual(Path(result["result_zip_path"]), pdf.with_suffix(".zip"))
            self.assertFalse(Path(result["result_zip_path"]).exists())

            manifest = read_json(result["manifest_path"], {})
            self.assertEqual(manifest["parser"], "pypdf")
            self.assertIn("pdf_sha256", manifest)
            self.assertEqual(manifest["result_zip_path"], str(pdf.with_suffix(".zip")))

            cache_paths = get_cached_paths(result["paper_key"], cache_dir=tmp)
            self.assertFalse(Path(cache_paths["source_pdf"]).exists())
            self.assertFalse(Path(cache_paths["full_md"]).exists())
            self.assertFalse(Path(cache_paths["content_list"]).exists())
            self.assertFalse(Path(cache_paths["assets_dir"]).exists())
            self.assertEqual(read_parsed(result["paper_key"], "markdown", cache_dir=tmp), Path(result["full_md_path"]).read_text(encoding="utf-8"))

    def test_cache_hit_skips_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)
            parser = MinerUParser(mode="pypdf", cache_dir=tmp)

            first = parser.parse_pdf(str(pdf), paper_key_hint="stable-key")
            second = parser.parse_pdf(str(pdf), paper_key_hint="stable-key")

            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["status"], "cached")
            self.assertEqual(second["paper_key"], "stable-key")
            self.assertFalse(Path(second["result_zip_path"]).exists())
            self.assertEqual(Path(second["full_md_path"]).parent, pdf.with_name("paper_mineru"))
            self.assertTrue(Path(second["full_md_path"]).exists())

    def test_cache_hit_re_exports_missing_visible_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)
            parser = MinerUParser(mode="pypdf", cache_dir=tmp)

            parser.parse_pdf(str(pdf), paper_key_hint="stable-key")
            visible_dir = pdf.with_name("paper_mineru")
            shutil.rmtree(visible_dir)

            result = parser.parse_pdf(str(pdf), paper_key_hint="stable-key")

            self.assertEqual(result["status"], "ok")
            self.assertTrue((visible_dir / "full.md").exists())
            self.assertTrue((visible_dir / "manifest.json").exists())
            self.assertFalse(pdf.with_suffix(".zip").exists())

    def test_export_zip_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)

            parser = MinerUParser(mode="pypdf", cache_dir=tmp)
            result = parser.parse_pdf(str(pdf), paper_key_hint="no-zip", force=True)

            self.assertEqual(result["status"], "ok")
            self.assertTrue(Path(result["full_md_path"]).exists())
            self.assertFalse(Path(result["result_zip_path"]).exists())

    def test_export_zip_can_be_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)

            with patch.dict("os.environ", {"PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP": "true"}):
                parser = MinerUParser(mode="pypdf", cache_dir=tmp)
                result = parser.parse_pdf(str(pdf), paper_key_hint="with-zip", force=True)

            self.assertEqual(result["status"], "ok")
            self.assertTrue(Path(result["result_zip_path"]).exists())
            with zipfile.ZipFile(result["result_zip_path"]) as archive:
                names = set(archive.namelist())
            self.assertIn("full.md", names)
            self.assertIn("content_list.json", names)
            self.assertIn("manifest.json", names)
            self.assertIn("metadata.json", names)
            self.assertIn("status.json", names)

    def test_extract_oss_no_proxy_is_added_by_default(self):
        with patch.dict(os.environ, {"NO_PROXY": "localhost"}, clear=True):
            MinerUParser(mode="extract", api_key="test-token")

            self.assertEqual(
                os.environ["NO_PROXY"],
                "localhost,.aliyuncs.com,mineru.oss-cn-shanghai.aliyuncs.com",
            )
            self.assertEqual(
                os.environ["no_proxy"],
                "localhost,.aliyuncs.com,mineru.oss-cn-shanghai.aliyuncs.com",
            )

    def test_extract_oss_no_proxy_can_be_disabled(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_MINERU_OSS_NO_PROXY": "false",
                "NO_PROXY": "localhost",
            },
            clear=True,
        ):
            MinerUParser(mode="extract", api_key="test-token")

            self.assertEqual(os.environ["NO_PROXY"], "localhost")
            self.assertEqual(os.environ["no_proxy"], "localhost")

    def test_extract_oss_no_proxy_hosts_can_be_customized(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_MINERU_OSS_NO_PROXY_HOSTS": ".aliyuncs.com,example.test",
                "NO_PROXY": ".aliyuncs.com",
            },
            clear=True,
        ):
            MinerUParser(mode="extract", api_key="test-token")

            self.assertEqual(os.environ["NO_PROXY"], ".aliyuncs.com,example.test")
            self.assertEqual(os.environ["no_proxy"], ".aliyuncs.com,example.test")

    def test_auto_mode_uses_extract_local_cli_then_pypdf_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)
            parser = MinerUParser(mode="auto", api_key="test-token", cache_dir=tmp)

            with patch.object(parser, "_parse_with_extract_api", side_effect=RuntimeError("upload failed")) as extract_mock, patch.object(
                parser, "_parse_with_local_api", side_effect=RuntimeError("local failed")
            ) as local_mock, patch.object(
                parser, "_parse_with_cli", side_effect=RuntimeError("cli failed")
            ) as cli_mock:
                result = parser.parse_pdf(str(pdf), paper_key_hint="auto-api", force=True)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parser"], "pypdf")
            self.assertEqual(result["mode"], "pypdf")
            self.assertIn("extract: upload failed", result["message"])
            self.assertIn("local_api: local failed", result["message"])
            self.assertIn("cli: cli failed", result["message"])
            extract_mock.assert_called_once()
            local_mock.assert_called_once()
            cli_mock.assert_called_once()

    def test_auto_mode_without_api_key_tries_local_cli_then_pypdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)
            parser = MinerUParser(mode="auto", cache_dir=tmp)
            parser.api_key = ""

            with patch.object(
                parser, "_parse_with_local_api", side_effect=RuntimeError("local failed")
            ) as local_mock, patch.object(
                parser, "_parse_with_cli", side_effect=RuntimeError("cli failed")
            ) as cli_mock:
                result = parser.parse_pdf(str(pdf), paper_key_hint="auto-no-key", force=True)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parser"], "pypdf")
            self.assertIn("local_api: local failed", result["message"])
            self.assertIn("cli: cli failed", result["message"])
            local_mock.assert_called_once()
            cli_mock.assert_called_once()

    def test_health_check_auto_reports_configured_order_and_probes_local_api_or_cli(self):
        parser = MinerUParser(mode="auto", api_key="test-token")

        response = Mock()
        response.status_code = 200
        with patch("paper_search_mcp.parsers.mineru.requests.get", return_value=response) as get_mock, patch(
            "paper_search_mcp.parsers.mineru.shutil.which", return_value="mineru"
        ) as which_mock:
            result = parser.health_check()

        self.assertEqual(result["auto_order"], ["extract", "local_api", "cli", "pypdf"])
        self.assertTrue(result["extract_api"]["ok"])
        self.assertEqual(result["local_api"]["message"], "200")
        self.assertEqual(result["cli"]["message"], "mineru")
        get_mock.assert_called_once()
        which_mock.assert_called_once()

    def test_legacy_cache_hit_exports_old_cached_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)
            parser = MinerUParser(mode="pypdf", cache_dir=tmp)

            first = parser.parse_pdf(str(pdf), paper_key_hint="stable-key")
            cache_paths = get_cached_paths("stable-key", cache_dir=tmp)
            Path(cache_paths["full_md"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(first["full_md_path"], cache_paths["full_md"])
            shutil.copy2(first["content_list_path"], cache_paths["content_list"])
            shutil.copy2(first["manifest_path"], cache_paths["manifest"])
            shutil.rmtree(pdf.with_name("paper_mineru"))

            result = parser.parse_pdf(str(pdf), paper_key_hint="stable-key")

            self.assertEqual(result["status"], "cached")
            self.assertTrue(Path(result["full_md_path"]).exists())
            self.assertTrue(Path(result["manifest_path"]).exists())

    def test_extract_api_downloads_full_zip_and_exports_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)

            mineru_zip = self._zip_bytes(
                {
                    "paper/full.md": b"# Parsed Paper\n\n![Figure](images/figure1.png)",
                    "paper/paper_content_list.json": (
                        b'[{"type":"image","text":"figure","img_path":"images/figure1.png"}]'
                    ),
                    "paper/images/figure1.png": b"\x89PNG\r\n\x1a\n",
                }
            )

            post_response = Mock()
            post_response.json.return_value = {
                "code": 0,
                "data": {
                    "batch_id": "batch-1",
                    "file_urls": [{"upload_url": "https://upload.example/paper.pdf"}],
                },
            }
            post_response.raise_for_status.return_value = None

            put_response = Mock()
            put_response.raise_for_status.return_value = None

            status_response = Mock()
            status_response.json.return_value = {
                "code": 0,
                "data": {
                    "extract_result": [
                        {
                            "data_id": "paper",
                            "state": "done",
                            "full_zip_url": "https://cdn.example/mineru.zip",
                        }
                    ]
                },
            }
            status_response.raise_for_status.return_value = None

            zip_response = Mock()
            zip_response.content = mineru_zip
            zip_response.headers = {"content-type": "application/zip"}
            zip_response.raise_for_status.return_value = None

            def fake_get(url, **kwargs):
                if "extract-results" in url:
                    return status_response
                return zip_response

            parser = MinerUParser(
                mode="extract",
                api_key="test-token",
                cache_dir=tmp,
                timeout=30,
            )

            with patch("paper_search_mcp.parsers.mineru.requests.post", return_value=post_response) as post_mock, patch(
                "paper_search_mcp.parsers.mineru.requests.put", return_value=put_response
            ) as put_mock, patch("paper_search_mcp.parsers.mineru.requests.get", side_effect=fake_get) as get_mock:
                result = parser.parse_pdf(str(pdf), paper_key_hint="extract-test", force=True)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["mode"], "extract")
            self.assertEqual(Path(result["result_zip_path"]), pdf.with_suffix(".zip"))
            self.assertFalse(Path(result["result_zip_path"]).exists())
            self.assertEqual(Path(result["manifest_path"]).parent, pdf.with_name("paper_mineru"))
            self.assertTrue(Path(result["full_md_path"]).read_text(encoding="utf-8").startswith("# Parsed Paper"))

            post_payload = post_mock.call_args.kwargs["json"]
            self.assertEqual(post_payload["files"][0]["name"], "paper.pdf")
            self.assertEqual(post_payload["model_version"], "vlm")
            self.assertTrue(post_mock.call_args.kwargs["headers"]["Authorization"].startswith("Bearer "))
            self.assertEqual(put_mock.call_args.args[0], "https://upload.example/paper.pdf")
            self.assertGreaterEqual(get_mock.call_count, 2)

            mineru_dir = Path(result["manifest_path"]).parent
            self.assertFalse((mineru_dir / "raw").exists())

    def test_extract_api_batch_parses_multiple_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf1 = Path(tmp) / "paper-one.pdf"
            pdf2 = Path(tmp) / "paper-two.pdf"
            _make_pdf(pdf1)
            _make_pdf(pdf2)

            zip_one = self._zip_bytes({"paper/full.md": b"# One\n\nBatch alpha"})
            zip_two = self._zip_bytes({"paper/full.md": b"# Two\n\nBatch beta"})

            post_response = Mock()
            post_response.json.return_value = {
                "code": 0,
                "data": {
                    "batch_id": "batch-2",
                    "file_urls": [
                        {"upload_url": "https://upload.example/one.pdf"},
                        {"upload_url": "https://upload.example/two.pdf"},
                    ],
                },
            }
            post_response.raise_for_status.return_value = None

            put_response = Mock()
            put_response.raise_for_status.return_value = None

            def fake_get(url, **kwargs):
                response = Mock()
                response.raise_for_status.return_value = None
                if "extract-results" in url:
                    data_ids = [item["data_id"] for item in fake_get.batch_payload["files"]]
                    response.json.return_value = {
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {"data_id": data_ids[0], "state": "done", "full_zip_url": "https://cdn.example/one.zip"},
                                {"data_id": data_ids[1], "state": "done", "full_zip_url": "https://cdn.example/two.zip"},
                            ]
                        },
                    }
                    return response
                response.headers = {"content-type": "application/zip"}
                response.content = zip_one if "one.zip" in url else zip_two
                return response

            parser = MinerUParser(mode="extract", api_key="test-token", cache_dir=tmp, timeout=30)

            def fake_post(url, **kwargs):
                fake_get.batch_payload = kwargs["json"]
                return post_response

            with patch("paper_search_mcp.parsers.mineru.requests.post", side_effect=fake_post) as post_mock, patch(
                "paper_search_mcp.parsers.mineru.requests.put", return_value=put_response
            ) as put_mock, patch("paper_search_mcp.parsers.mineru.requests.get", side_effect=fake_get):
                results = parser.parse_pdfs(
                    [
                        {"pdf_path": str(pdf1), "paper_key": "batch-one", "title": "Batch One"},
                        {"pdf_path": str(pdf2), "paper_key": "batch-two", "title": "Batch Two"},
                    ],
                    force=True,
                )

            self.assertEqual([result["status"] for result in results], ["ok", "ok"])
            self.assertEqual([result["mode"] for result in results], ["extract", "extract"])
            self.assertEqual(post_mock.call_args.kwargs["json"]["files"][0]["name"], "paper-one.pdf")
            self.assertEqual(len(post_mock.call_args.kwargs["json"]["files"]), 2)
            self.assertEqual(put_mock.call_count, 2)
            self.assertIn("Batch alpha", Path(results[0]["full_md_path"]).read_text(encoding="utf-8"))
            self.assertIn("Batch beta", Path(results[1]["full_md_path"]).read_text(encoding="utf-8"))

    def test_extract_api_batch_reuses_downloaded_zip_on_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf1 = Path(tmp) / "paper-one.pdf"
            pdf2 = Path(tmp) / "paper-two.pdf"
            _make_pdf(pdf1)
            _make_pdf(pdf2)

            zip_one = self._zip_bytes({"paper/full.md": b"# One\n\nReusable alpha"})
            zip_two = self._zip_bytes({"paper/full.md": b"# Two\n\nReusable beta"})

            post_response = Mock()
            post_response.json.return_value = {
                "code": 0,
                "data": {
                    "batch_id": "batch-reuse",
                    "file_urls": [
                        {"upload_url": "https://upload.example/one.pdf"},
                        {"upload_url": "https://upload.example/two.pdf"},
                    ],
                },
            }
            post_response.raise_for_status.return_value = None
            put_response = Mock()
            put_response.raise_for_status.return_value = None

            def fake_post(url, **kwargs):
                fake_get.batch_payload = kwargs["json"]
                return post_response

            def fake_get(url, **kwargs):
                response = Mock()
                response.raise_for_status.return_value = None
                if "extract-results" in url:
                    data_ids = [item["data_id"] for item in fake_get.batch_payload["files"]]
                    response.json.return_value = {
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {"data_id": data_ids[0], "state": "done", "full_zip_url": "https://cdn.example/one.zip"},
                                {"data_id": data_ids[1], "state": "done", "full_zip_url": "https://cdn.example/two.zip"},
                            ]
                        },
                    }
                    return response
                response.headers = {"content-type": "application/zip"}
                response.content = zip_one if "one.zip" in url else zip_two
                return response

            parser = MinerUParser(mode="extract", api_key="test-token", cache_dir=tmp, timeout=30)
            items = [
                {"pdf_path": str(pdf1), "paper_key": "reuse-one", "title": "Reuse One"},
                {"pdf_path": str(pdf2), "paper_key": "reuse-two", "title": "Reuse Two"},
            ]
            with patch("paper_search_mcp.parsers.mineru.requests.post", side_effect=fake_post), patch(
                "paper_search_mcp.parsers.mineru.requests.put", return_value=put_response
            ), patch("paper_search_mcp.parsers.mineru.requests.get", side_effect=fake_get):
                first = parser.parse_pdfs(items, force=True)

            self.assertEqual([result["status"] for result in first], ["ok", "ok"])
            self.assertTrue(list((Path(tmp) / "mineru_batches").rglob("*.zip")))
            shutil.rmtree(pdf1.with_name("paper-one_mineru"))
            shutil.rmtree(pdf2.with_name("paper-two_mineru"))

            with patch(
                "paper_search_mcp.parsers.mineru.requests.post",
                side_effect=AssertionError("batch submit should be skipped when reusable zip exists"),
            ):
                second = parser.parse_pdfs(items, force=False)

            self.assertEqual([result["status"] for result in second], ["ok", "ok"])
            self.assertIn("Reusable alpha", Path(second[0]["full_md_path"]).read_text(encoding="utf-8"))

    def test_cli_mode_uses_temporary_raw_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            _make_pdf(pdf)

            parser = MinerUParser(mode="cli", cache_dir=tmp)

            def fake_run(cmd, **kwargs):
                raw_dir = Path(cmd[cmd.index("-o") + 1])
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / "full.md").write_text("# CLI Parsed", encoding="utf-8")
                (raw_dir / "content_list.json").write_text('[{"type":"text","text":"CLI Parsed"}]', encoding="utf-8")
                (raw_dir / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n")
                completed = Mock()
                completed.returncode = 0
                completed.stdout = ""
                completed.stderr = ""
                return completed

            with patch("paper_search_mcp.parsers.mineru.shutil.which", return_value="mineru"), patch(
                "paper_search_mcp.parsers.mineru.subprocess.run", side_effect=fake_run
            ):
                result = parser.parse_pdf(str(pdf), paper_key_hint="cli-temp", force=True)

            cache_paths = get_cached_paths("cli-temp", cache_dir=tmp)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["mode"], "cli")
            self.assertEqual(Path(result["manifest_path"]).parent, pdf.with_name("paper_mineru"))
            self.assertTrue(Path(result["assets_dir"], "figures", "figure.png").exists())
            self.assertFalse((Path(cache_paths["mineru_dir"]) / "raw_cli").exists())
            self.assertFalse((Path(cache_paths["mineru_dir"]) / "raw").exists())


if __name__ == "__main__":
    unittest.main()
