import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from paper_search_mcp.cache import read_json
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
            self.assertEqual(Path(result["result_zip_path"]), pdf.with_suffix(".zip"))
            self.assertTrue(Path(result["result_zip_path"]).exists())

            with zipfile.ZipFile(result["result_zip_path"]) as archive:
                names = set(archive.namelist())
            self.assertIn("full.md", names)
            self.assertIn("content_list.json", names)
            self.assertIn("manifest.json", names)
            self.assertIn("metadata.json", names)
            self.assertIn("status.json", names)

            manifest = read_json(result["manifest_path"], {})
            self.assertEqual(manifest["parser"], "pypdf")
            self.assertIn("pdf_sha256", manifest)
            self.assertEqual(manifest["result_zip_path"], str(pdf.with_suffix(".zip")))

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
            self.assertTrue(Path(second["result_zip_path"]).exists())

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
            self.assertTrue(Path(result["result_zip_path"]).exists())
            self.assertTrue(Path(result["full_md_path"]).read_text(encoding="utf-8").startswith("# Parsed Paper"))

            post_payload = post_mock.call_args.kwargs["json"]
            self.assertEqual(post_payload["files"][0]["name"], "paper.pdf")
            self.assertEqual(post_payload["model_version"], "vlm")
            self.assertTrue(post_mock.call_args.kwargs["headers"]["Authorization"].startswith("Bearer "))
            self.assertEqual(put_mock.call_args.args[0], "https://upload.example/paper.pdf")
            self.assertGreaterEqual(get_mock.call_count, 2)

            with zipfile.ZipFile(result["result_zip_path"]) as archive:
                names = set(archive.namelist())
            self.assertIn("full.md", names)
            self.assertIn("content_list.json", names)
            self.assertIn("assets/figures/figure1.png", names)
            self.assertIn("raw/zip/paper/images/figure1.png", names)


if __name__ == "__main__":
    unittest.main()
