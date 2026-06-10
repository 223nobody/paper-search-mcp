import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from paper_search_mcp import cache, server


class TestSelectionSessions(unittest.TestCase):
    def test_parse_selected_indices_accepts_text_all_and_ranges(self):
        self.assertEqual(server._parse_selected_indices("all", 3), [1, 2, 3])
        self.assertEqual(server._parse_selected_indices("1, 3", 4), [1, 3])
        self.assertEqual(server._parse_selected_indices("2-4", 5), [2, 3, 4])
        self.assertEqual(server._parse_selected_indices([1, 2, 2], 3), [1, 2])

        with self.assertRaises(ValueError):
            server._parse_selected_indices("0", 3)

    def test_elicitation_schema_uses_array_enum(self):
        schema = server._build_paper_selection_schema(["1. First", "2. Second"])
        selected = schema.model_json_schema()["properties"]["selected_papers"]
        self.assertEqual(selected["type"], "array")
        self.assertEqual(selected["items"]["enum"], ["1. First", "2. Second"])

    def test_parse_elicitation_selected_indices(self):
        selected = ["2. Second Paper [arxiv, 2026]", "1. First Paper [semantic, 2025]"]
        self.assertEqual(server._parse_elicitation_selected_indices(selected, 3), [2, 1])

    def test_search_papers_for_parsing_creates_numbered_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "scene aware skills",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": 1},
                "errors": {},
                "papers": [
                    {
                        "title": "Scene-Aware Skills",
                        "authors": "A. Researcher",
                        "published_date": "2026-01-02",
                        "source": "arxiv",
                        "paper_id": "2601.00001",
                        "doi": "",
                        "pdf_url": "https://example.org/paper.pdf",
                        "url": "https://example.org/paper",
                    }
                ],
                "total": 1,
                "raw_total": 1,
            }

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.search_papers",
                new=AsyncMock(return_value=fake_search_result),
            ):
                result = asyncio.run(
                    server.search_papers_for_parsing(
                        "scene aware skills",
                        max_results_per_source=1,
                        sources="arxiv",
                    )
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["papers"][0]["index"], 1)
            self.assertEqual(result["papers"][0]["reason"], "direct_pdf_url")

            loaded = cache.get_search_session(result["selection_token"], cache_dir=tmp)
            self.assertEqual(loaded["papers"][0]["title"], "Scene-Aware Skills")

    def test_parse_selected_papers_uses_session_and_direct_pdf_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "downloaded.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="mineru parsing",
                sources="arxiv",
                papers=[
                    {
                        "title": "MinerU Parsing Paper",
                        "authors": "A. Researcher",
                        "source": "arxiv",
                        "paper_id": "2601.00002",
                        "doi": "10.1000/mineru",
                        "pdf_url": "https://example.org/mineru.pdf",
                        "url": "https://example.org/mineru",
                    }
                ],
                cache_dir=tmp,
            )

            parse_payload = {
                "status": "ok",
                "full_md_path": str(Path(tmp) / "full.md"),
                "result_zip_path": str(Path(tmp) / "downloaded.zip"),
            }

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.parse_pdf_with_mineru",
                new=AsyncMock(return_value=parse_payload),
            ):
                result = asyncio.run(
                    server.parse_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        mode="pypdf",
                    )
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parsed"], 1)
            self.assertEqual(result["results"][0]["download_method"], "search_result_pdf_url")
            self.assertEqual(result["results"][0]["parse"]["result_zip_path"], parse_payload["result_zip_path"])

    def test_after_saved_pdf_creates_numbered_session_without_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "saved.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                result = asyncio.run(
                    server._after_saved_pdf(
                        str(pdf),
                        source="arxiv",
                        paper_id="2601.00005",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                    )
                )

            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(result["pdf_path"], str(pdf.resolve()))
            prompt = result["parse_prompt"]
            self.assertEqual(prompt["status"], "elicitation_unavailable")
            self.assertEqual(prompt["interaction"], "backend_session_numbered_selection")
            self.assertEqual(prompt["papers"][0]["reason"], "local_pdf_path")
            self.assertEqual(prompt["papers"][0]["local_pdf_path"], str(pdf.resolve()))

            loaded = cache.get_search_session(prompt["selection_token"], cache_dir=tmp)
            self.assertEqual(loaded["metadata"]["trigger"], "pdf_saved")
            self.assertEqual(loaded["papers"][0]["local_pdf_path"], str(pdf.resolve()))

    def test_after_saved_pdf_elicitation_accepts_and_parses_selection(self):
        class FakeData:
            selected_papers = ["1. Saved PDF [arxiv, n.d., 2601.00006]"]

        class FakeElicitation:
            action = "accept"
            data = FakeData()

        class FakeContext:
            def __init__(self):
                self.schema = None

            async def elicit(self, message, schema):
                self.schema = schema
                return FakeElicitation()

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "saved.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            parse_payload = {
                "status": "ok",
                "results": [{"status": "ok"}],
                "total": 1,
                "parsed": 1,
                "failed": 0,
                "skipped": 0,
            }
            ctx = FakeContext()

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(return_value=parse_payload),
            ) as parse_mock:
                result = asyncio.run(
                    server._after_saved_pdf(
                        str(pdf),
                        source="arxiv",
                        paper_id="2601.00006",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=ctx,
                    )
                )

            self.assertEqual(result["parse_prompt"]["interaction"], "elicitation")
            self.assertEqual(result["parse_prompt"]["selected_indices"], [1])
            parse_mock.assert_awaited_once()
            self.assertEqual(parse_mock.await_args.kwargs["selected_indices"], "1")
            schema_items = ctx.schema.model_json_schema()["properties"]["selected_papers"]["items"]
            self.assertIn("1. Saved PDF", schema_items["enum"][0])

    def test_read_source_paper_detects_saved_pdf_and_prompts(self):
        class FakeSearcher:
            def read_paper(self, paper_id, save_path):
                Path(save_path).mkdir(parents=True, exist_ok=True)
                (Path(save_path) / "read-saved.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
                return "extracted text"

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                result = asyncio.run(
                    server._read_source_paper(
                        FakeSearcher(),
                        source="arxiv",
                        paper_id="2601.00007",
                        save_path=tmp,
                        ctx=None,
                    )
                )

            self.assertEqual(result["status"], "read")
            self.assertEqual(result["text"], "extracted text")
            prompt = result["saved_pdf_prompt"]["parse_prompt"]
            self.assertEqual(prompt["status"], "elicitation_unavailable")
            self.assertEqual(prompt["papers"][0]["reason"], "local_pdf_path")

    def test_search_papers_with_elicitation_accepts_selection(self):
        class FakeData:
            selected_papers = ["1. Selected Paper [arxiv, 2026, 2601.00003]"]

        class FakeElicitation:
            action = "accept"
            data = FakeData()

        class FakeContext:
            def __init__(self):
                self.schema = None

            async def elicit(self, message, schema):
                self.schema = schema
                return FakeElicitation()

        fake_session_result = {
            "status": "ok",
            "selection_token": "search_test_token",
            "query": "elicitation paper",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": 1},
            "errors": {},
            "papers": [
                {
                    "index": 1,
                    "title": "Selected Paper",
                    "authors": "A. Researcher",
                    "year": "2026",
                    "source": "arxiv",
                    "paper_id": "2601.00003",
                    "doi": "",
                    "pdf_url": "https://example.org/selected.pdf",
                    "url": "https://example.org/selected",
                    "parse_ready": True,
                    "reason": "direct_pdf_url",
                }
            ],
            "total": 1,
            "parse_ready_total": 1,
        }
        fake_parse_result = {
            "status": "ok",
            "selection_token": "search_test_token",
            "selected_indices": [1],
            "results": [{"status": "ok"}],
            "total": 1,
            "parsed": 1,
            "failed": 0,
            "skipped": 0,
        }
        ctx = FakeContext()

        with patch(
            "paper_search_mcp.server.search_papers_for_parsing",
            new=AsyncMock(return_value=fake_session_result),
        ), patch(
            "paper_search_mcp.server.parse_selected_papers",
            new=AsyncMock(return_value=fake_parse_result),
        ) as parse_mock:
            result = asyncio.run(
                server.search_papers_with_elicitation(
                    "elicitation paper",
                    sources="arxiv",
                    ctx=ctx,
                    mode="pypdf",
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["interaction"], "elicitation")
        self.assertEqual(result["selected_indices"], [1])
        parse_mock.assert_awaited_once()
        self.assertEqual(parse_mock.await_args.kwargs["selection_token"], "search_test_token")
        self.assertEqual(parse_mock.await_args.kwargs["selected_indices"], "1")
        schema_items = ctx.schema.model_json_schema()["properties"]["selected_papers"]["items"]
        self.assertIn("1. Selected Paper", schema_items["enum"][0])

    def test_search_papers_with_elicitation_falls_back_without_context(self):
        fake_session_result = {
            "status": "ok",
            "selection_token": "search_test_token",
            "query": "fallback paper",
            "papers": [
                {
                    "index": 1,
                    "title": "Fallback Paper",
                    "source": "arxiv",
                    "paper_id": "2601.00004",
                    "parse_ready": True,
                }
            ],
            "total": 1,
            "parse_ready_total": 1,
        }

        with patch(
            "paper_search_mcp.server.search_papers_for_parsing",
            new=AsyncMock(return_value=fake_session_result),
        ):
            result = asyncio.run(
                server.search_papers_with_elicitation(
                    "fallback paper",
                    sources="arxiv",
                    ctx=None,
                )
            )

        self.assertEqual(result["status"], "elicitation_unavailable")
        self.assertEqual(result["interaction"], "backend_session_numbered_selection")
        self.assertEqual(result["selection_token"], "search_test_token")


if __name__ == "__main__":
    unittest.main()
