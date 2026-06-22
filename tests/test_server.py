# tests/test_server.py
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pypdf import PdfWriter

from paper_search_mcp import server
from paper_search_mcp import cli

class TestPaperSearchServer(unittest.TestCase):
    def test_all_sources_include_new_platforms(self):
        self.assertIn("dblp", server.ALL_SOURCES)
        self.assertIn("openaire", server.ALL_SOURCES)
        self.assertIn("citeseerx", server.ALL_SOURCES)
        self.assertIn("doaj", server.ALL_SOURCES)
        self.assertIn("base", server.ALL_SOURCES)
        self.assertIn("zenodo", server.ALL_SOURCES)
        self.assertIn("hal", server.ALL_SOURCES)
        self.assertIn("ssrn", server.ALL_SOURCES)
        self.assertIn("unpaywall", server.ALL_SOURCES)

    def test_parse_sources_with_new_platforms(self):
        with patch.dict(os.environ, {}, clear=True):
            parsed = server._parse_sources("dblp,doaj,base,zenodo,hal,ssrn,unpaywall,invalid")
        self.assertEqual(parsed, ["dblp", "doaj", "base", "zenodo", "hal", "ssrn", "unpaywall"])

    def test_parse_sources_defaults_to_fast_profile(self):
        with patch.dict(os.environ, {"PAPER_SEARCH_MCP_SEARCH_PROFILE": "fast"}, clear=True):
            parsed = server._parse_sources("")

        self.assertEqual(parsed, server.FAST_SOURCES)

    def test_parse_sources_filters_disabled_sources(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_SEARCH_PROFILE": "agent-skill-broad",
                "PAPER_SEARCH_MCP_DISABLED_SOURCES": "semantic,google_scholar",
            },
        ):
            parsed = server._parse_sources("")

        self.assertIn("arxiv", parsed)
        self.assertNotIn("semantic", parsed)
        self.assertNotIn("google_scholar", parsed)

    def test_pdf_cs_profile_prioritizes_smoke_tested_sources(self):
        with patch.dict(os.environ, {"PAPER_SEARCH_MCP_DISABLED_SOURCES": ""}, clear=True):
            parsed = server._parse_sources("pdf-cs")

        self.assertEqual(parsed, ["arxiv", "openalex", "crossref", "dblp"])

    def test_cli_parse_sources_defaults_to_fast_profile(self):
        with patch.dict(os.environ, {"PAPER_SEARCH_MCP_SEARCH_PROFILE": "fast"}, clear=True):
            cli.SEARCHERS.clear()
            cli._init_searchers()
            parsed = cli._parse_sources("")

        self.assertEqual(parsed, [source for source in cli.FAST_SOURCES if source in cli.SEARCHERS])
        self.assertNotIn("google_scholar", parsed)

    def test_cli_search_reuses_server_search_papers(self):
        args = type(
            "Args",
            (),
            {
                "query": "agent skill",
                "sources": "arxiv,semantic",
                "max_results": 7,
                "year": "2026",
            },
        )()
        payload = {
            "query": "agent skill",
            "sources_used": ["arxiv", "semantic"],
            "source_results": {"arxiv": 1},
            "errors": {},
            "papers": [{"title": "Agent Skill Libraries"}],
            "total": 1,
        }

        with patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=payload),
        ) as search_mock, patch(
            "builtins.print",
        ) as print_mock:
            exit_code = asyncio.run(cli.cmd_search(args))

        self.assertEqual(exit_code, 0)
        search_mock.assert_awaited_once_with(
            query="agent skill",
            max_results_per_source=7,
            sources="arxiv,semantic",
            year="2026",
        )
        self.assertEqual(json.loads(print_mock.call_args.args[0]), payload)

    def test_search_papers_uses_cache_for_repeated_query(self):
        async def fake_search_arxiv(query, max_results):
            return [{"title": "Cached Paper", "paper_id": "1", "source": "arxiv"}]

        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS": "300",
                "PAPER_SEARCH_MCP_SEARCH_TIMEOUT_SECONDS": "5",
            },
        ), patch(
            "paper_search_mcp.server.search_arxiv",
            new=AsyncMock(side_effect=fake_search_arxiv),
        ) as search_mock:
            server._SEARCH_RESULT_CACHE.clear()
            first = asyncio.run(server.search_papers("cached query", sources="arxiv", max_results_per_source=1))
            second = asyncio.run(server.search_papers("cached query", sources="arxiv", max_results_per_source=1))

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(second["total"], 1)
        search_mock.assert_awaited_once()

    def test_dedupe_papers_merges_sources_and_scores_pdf_results(self):
        papers = [
            {
                "title": "Neural Retrieval Systems",
                "authors": "A. Author",
                "source": "semantic",
                "paper_id": "s1",
                "doi": "10.1000/retrieval",
            },
            {
                "title": "Neural Retrieval Systems",
                "authors": "A. Author",
                "source": "arxiv",
                "paper_id": "a1",
                "doi": "10.1000/retrieval",
                "pdf_url": "https://example.org/paper.pdf",
            },
        ]

        deduped = server._dedupe_papers(papers, query="neural retrieval")

        self.assertEqual(len(deduped), 1)
        self.assertIn("semantic", deduped[0]["sources"])
        self.assertIn("arxiv", deduped[0]["sources"])
        self.assertGreater(deduped[0]["score"], 0)
        self.assertEqual(deduped[0]["pdf_url"], "https://example.org/paper.pdf")

    def test_dedupe_prefers_reliable_pdf_sources_for_similar_records(self):
        papers = [
            {
                "title": "Reliable PDF Ranking",
                "authors": "A. Author",
                "source": "dblp",
                "paper_id": "d1",
                "doi": "10.1000/ranking",
            },
            {
                "title": "Reliable PDF Ranking",
                "authors": "B. Author",
                "source": "arxiv",
                "paper_id": "2601.00001",
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
            },
        ]

        deduped = server._dedupe_papers(papers, query="reliable pdf ranking")

        self.assertEqual(deduped[0]["source"], "arxiv")
        self.assertGreater(deduped[0]["score"], deduped[1]["score"])

    def test_list_sources_exposes_capabilities(self):
        result = asyncio.run(server.list_sources())
        self.assertIn("sources", result)
        arxiv = next(source for source in result["sources"] if source["name"] == "arxiv")
        self.assertTrue(arxiv["search"])
        self.assertTrue(arxiv["download"])
        self.assertGreater(arxiv["reliability"]["score"], 90)
        self.assertEqual(result["sources_ranked_by_reliability"][0], "arxiv")

    def test_diagnose_paper_sources_reports_config_and_disabled_sources(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_DISABLED_SOURCES": "semantic",
                "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "dev@example.test",
            },
        ):
            result = asyncio.run(server.diagnose_paper_sources("semantic,unpaywall,zenodo"))

        self.assertEqual(result["status"], "ok")
        self.assertIn("semantic", result["disabled_sources"])
        semantic = next(source for source in result["sources"] if source["source"] == "semantic")
        unpaywall = next(source for source in result["sources"] if source["source"] == "unpaywall")
        self.assertTrue(semantic["disabled"])
        self.assertTrue(unpaywall["config"]["configured"])
        self.assertIn("sources_ranked_by_reliability", result)

    def test_source_from_identifier_routes_zenodo_doi(self):
        routed = server._source_from_identifier("semantic", "DOI:10.5281/zenodo.20117466", "")
        self.assertEqual(routed, ("zenodo", "10.5281/zenodo.20117466", "10.5281/zenodo.20117466"))

    def test_download_semantic_routes_zenodo_doi_to_zenodo_downloader(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "zenodo_20117466.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            def fake_download(paper_id, save_path):
                self.assertEqual(paper_id, "10.5281/zenodo.20117466")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_PROMPT": "false",
                },
            ), patch.object(
                server.zenodo_searcher,
                "download_pdf",
                side_effect=fake_download,
            ), patch.object(
                server.semantic_searcher,
                "download_pdf",
                side_effect=AssertionError("Zenodo DOI should not use Semantic Scholar openAccessPdf"),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-zenodo"}),
            ):
                result = asyncio.run(
                    server.download_semantic(
                        "DOI:10.5281/zenodo.20117466",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["source"], "zenodo")
        self.assertEqual(result["paper_id"], "10.5281/zenodo.20117466")
        self.assertEqual(result["doi"], "10.5281/zenodo.20117466")

    def test_parse_pdf_with_mineru_pypdf_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf.open("wb") as fh:
                writer.write(fh)

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                result = asyncio.run(
                    server.parse_pdf_with_mineru(
                        str(pdf),
                        paper_key="server-test",
                        mode="pypdf",
                        force=True,
                    )
                )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(Path(result["full_md_path"]).exists())

    def test_download_from_url_streams_to_temp_then_final_pdf(self):
        class FakeStreamResponse:
            status_code = 200
            headers = {"content-type": "application/pdf"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aiter_bytes(self, chunk_size=0):
                yield b"%PDF-1.4\n"
                yield b"%%EOF"

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url):
                return FakeStreamResponse()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "paper_search_mcp.server.httpx.AsyncClient",
            FakeClient,
        ):
            result = asyncio.run(server._download_from_url("https://example.org/paper.pdf", tmp, "streamed"))
            self.assertTrue(result.endswith("streamed.pdf"))
            self.assertTrue(Path(result).exists())
            self.assertTrue(Path(result).read_bytes().startswith(b"%PDF"))

    def test_search_arxiv(self):
        """Test the search_arxiv tool returns 10 results."""
        result = asyncio.run(server.search_arxiv("machine learning", max_results=10))
        self.assertIsInstance(result, list, "Result should be a list")
        self.assertEqual(len(result), 10, "Should return exactly 10 results")
        for paper in result:
            self.assertIn('title', paper, "Each result should contain a title")
            self.assertIn('paper_id', paper, "Each result should contain a paper_id")

    def test_download_arxiv_from_search(self):
        """Test downloading 10 arXiv papers based on search results."""
        # 先搜索 10 个结果
        search_results = asyncio.run(server.search_arxiv("machine learning", max_results=10))
        self.assertEqual(len(search_results), 10, "Search should return 10 results")

        # 下载每个搜索结果的 PDF
        parse_payload = {
            "status": "ok",
            "results": [{"status": "ok"}],
            "total": 1,
            "parsed": 1,
            "failed": 0,
            "skipped": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(Path(tmp) / "missing.env"),
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_PROMPT": "false",
                    "USERPROFILE": tmp,
                },
                clear=True,
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-default"}),
            ):
                for paper in search_results:
                    paper_id = paper['paper_id']
                    result = asyncio.run(server.download_arxiv(paper_id))
                    self.assertIsInstance(result, dict, f"Result for {paper_id} should include download metadata")
                    self.assertEqual(result["status"], "downloaded")
                    self.assertTrue(result["pdf_path"].endswith(".pdf"), f"Result for {paper_id} should be a PDF file path")
                    self.assertTrue(os.path.exists(result["pdf_path"]), f"PDF file for {paper_id} should exist on disk")
                    self.assertEqual(Path(result["pdf_path"]).parent, (Path(tmp) / "Desktop" / "papers").resolve())
                    self.assertEqual(result["parse_prompt"]["interaction"], "auto_parse_saved_pdfs")
                    self.assertEqual(result["parse_prompt"]["selected_indices"], [1])
                    self.assertEqual(result["parse_prompt"]["parse_job"]["job_id"], "parse-default")

    def test_download_arxiv_requires_custom_save_path_confirmation_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(Path(tmp) / "missing.env"),
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "USERPROFILE": tmp,
                },
                clear=True,
            ), patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=AssertionError("download should not start without save_path confirmation"),
            ):
                result = asyncio.run(server.download_arxiv("2601.00001", tmp))

        self.assertEqual(result["status"], "invalid_save_path")
        self.assertEqual(result["confirm_param"], "custom_save_path_confirmed")

    def test_download_arxiv_allows_confirmed_custom_save_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_download(paper_id, save_path, **kwargs):
                target = Path(save_path) / f"{paper_id}.pdf"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(target)

            parse_payload = {
                "status": "ok",
                "results": [{"status": "ok"}],
                "total": 1,
                "parsed": 1,
                "failed": 0,
                "skipped": 0,
            }
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(Path(tmp) / "missing.env"),
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "USERPROFILE": tmp,
                },
                clear=True,
            ), patch.object(server.arxiv_searcher, "download_pdf", side_effect=fake_download), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-confirmed"}),
            ):
                result = asyncio.run(
                    server._download_source_pdf(
                        server.arxiv_searcher,
                        source="arxiv",
                        paper_id="2601.00001",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(Path(result["pdf_path"]).parent, Path(tmp).resolve())
        self.assertEqual(result["parse_prompt"]["interaction"], "auto_parse_saved_pdfs")
        self.assertEqual(result["parse_prompt"]["parse_job"]["job_id"], "parse-confirmed")
        self.assertNotIn("app", result)

    def test_download_arxiv_skip_parse_execution_only_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_download(paper_id, save_path, **kwargs):
                target = Path(save_path) / f"{paper_id}.pdf"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(target)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(Path(tmp) / "missing.env"),
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "USERPROFILE": tmp,
                },
                clear=True,
            ), patch.object(server.arxiv_searcher, "download_pdf", side_effect=fake_download), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("skip must not submit a parse job")),
            ):
                result = asyncio.run(server.download_arxiv("2601.00001", parse_execution="skip"))

        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(result["parse_prompt"]["status"], "ok")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")
        self.assertNotIn("parse_job", result["parse_prompt"])

    def test_download_arxiv_skips_existing_pdf_without_downloader_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "2601.00001.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=AssertionError("existing PDF should be reused"),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("skip must not submit a parse job")),
            ):
                result = asyncio.run(
                    server.download_arxiv(
                        "2601.00001",
                        save_path=tmp,
                        parse_execution="skip",
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "skipped_existing")
        self.assertEqual(result["download_method"], "existing_canonical")
        self.assertEqual(Path(result["pdf_path"]), pdf.resolve())
        self.assertTrue(result["valid_pdf"])
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")

    def test_download_arxiv_rejects_custom_save_path_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "false"}), patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=AssertionError("download should not start for rejected save_path"),
            ):
                result = asyncio.run(
                    server._download_source_pdf(
                        server.arxiv_searcher,
                        source="arxiv",
                        paper_id="2601.00001",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "invalid_save_path")
        self.assertEqual(result["allow_env"], "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH")

    def test_configure_mineru_api_key_writes_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = str(Path(tmp) / ".env")
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_ENV_FILE": env_path}, clear=True):
                result = asyncio.run(server.configure_mineru_api_key("test-mineru-key"))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["env_file_path"], env_path)
            self.assertIn("PAPER_SEARCH_MCP_MINERU_API_KEY=test-mineru-key", Path(env_path).read_text())

    def test_mineru_setup_status_returns_app_prompt_when_key_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = str(Path(tmp) / ".env")
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_ENV_FILE": env_path}, clear=True):
                result = asyncio.run(server.mineru_setup_status())

            self.assertEqual(result["status"], "mineru_api_key_required")
            self.assertFalse(result["configured"])
            self.assertEqual(result["render_tool"], server.MINERU_KEY_WIDGET_TOOL)
            self.assertEqual(result["resource_uri"], server.MINERU_KEY_WIDGET_URI)

    def test_mineru_health_check_returns_key_prompt_when_extract_key_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = str(Path(tmp) / ".env")
            health = {
                "mode": "auto",
                "extract_api": {"ok": False, "message": "PAPER_SEARCH_MCP_MINERU_API_KEY is not set"},
            }
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_ENV_FILE": env_path}, clear=True), patch(
                "paper_search_mcp.server.run_mineru_health_check",
                return_value=health,
            ):
                result = asyncio.run(server.mineru_health_check())

            self.assertIn("mineru_api_key_prompt", result)
            self.assertEqual(result["mineru_api_key_prompt"]["render_tool"], server.MINERU_KEY_WIDGET_TOOL)

    def test_http_transport_env_configuration(self):
        original = {
            "host": server.mcp.settings.host,
            "port": server.mcp.settings.port,
            "path": server.mcp.settings.streamable_http_path,
            "security": server.mcp.settings.transport_security,
        }
        try:
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_HOST": "0.0.0.0",
                    "PAPER_SEARCH_MCP_PORT": "8765",
                    "PAPER_SEARCH_MCP_MCP_PATH": "/mcp",
                    "PAPER_SEARCH_MCP_ALLOWED_HOSTS": "example.ngrok-free.app,example.com",
                    "PAPER_SEARCH_MCP_ALLOWED_ORIGINS": "https://chatgpt.com,https://example.com",
                },
                clear=False,
            ):
                server._configure_http_transport_from_env()

            self.assertEqual(server.mcp.settings.host, "0.0.0.0")
            self.assertEqual(server.mcp.settings.port, 8765)
            self.assertEqual(server.mcp.settings.streamable_http_path, "/mcp")
            security = server.mcp.settings.transport_security
            self.assertTrue(security.enable_dns_rebinding_protection)
            self.assertIn("example.ngrok-free.app", security.allowed_hosts)
            self.assertIn("https://chatgpt.com", security.allowed_origins)
        finally:
            server.mcp.settings.host = original["host"]
            server.mcp.settings.port = original["port"]
            server.mcp.settings.streamable_http_path = original["path"]
            server.mcp.settings.transport_security = original["security"]

    def test_parse_pdf_with_mineru_attaches_key_prompt_on_auth_failure(self):
        parse_error = {
            "status": "error",
            "message": "extract: 401 Unauthorized; token expired",
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(Path(tmp) / ".env"),
                    "PAPER_SEARCH_MCP_MINERU_API_KEY": "expired-key",
                },
                clear=True,
            ), patch(
                "paper_search_mcp.server.run_parse_pdf_with_mineru",
                return_value=parse_error,
            ):
                result = asyncio.run(server.parse_pdf_with_mineru(str(pdf), mode="extract"))

            self.assertIn("mineru_api_key_prompt", result)
            self.assertEqual(result["mineru_api_key_prompt"]["reason"], "expired_or_invalid")

    def test_mineru_api_key_widget_contains_input_and_config_tool_call(self):
        html = asyncio.run(server.mineru_api_key_setup_widget())
        self.assertIn('id="api-key"', html)
        self.assertIn("configure_mineru_api_key", html)

if __name__ == "__main__":
    unittest.main()
