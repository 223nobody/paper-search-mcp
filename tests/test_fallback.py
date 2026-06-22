import unittest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

from paper_search_mcp import server
from paper_search_mcp.academic_platforms.publisher_direct import resolve_publisher_direct_url
from paper_search_mcp.engine import download as download_engine


class TestDownloadWithFallback(unittest.TestCase):
    def test_default_does_not_call_scihub(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None), \
             patch("paper_search_mcp.server.SciHubFetcher.download_pdf", side_effect=AssertionError("Sci-Hub should be opt-in")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                )
            )
            self.assertIn("OA fallback chain", result)

    def test_repository_fallback_wins_oa_race_before_scihub(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_pdf = Path(tmp) / "repo.pdf"
            repo_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
                 patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(str(repo_pdf), ""))), \
                 patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None), \
                 patch("paper_search_mcp.server.SciHubFetcher.download_pdf", side_effect=AssertionError("Sci-Hub should not be called")):
                result = asyncio.run(
                    server._download_with_fallback_path(
                        source="arxiv",
                        paper_id="1234.5678",
                        doi="10.1000/test",
                        title="test",
                        save_path=tmp,
                        use_scihub=True,
                    )
                )
            self.assertEqual(result, str(repo_pdf))

    def test_unpaywall_fallback_can_win_oa_race(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value="https://example.org/oa.pdf"), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/unpaywall.pdf")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/unpaywall.pdf")

    def test_download_with_fallback_reports_routed_zenodo_doi_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "zenodo_20117466.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            with patch.dict(
                "os.environ",
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_PROMPT": "false",
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                },
            ), patch(
                "paper_search_mcp.server._download_with_fallback_path",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-zenodo"}),
            ):
                result = asyncio.run(
                    server.download_with_fallback(
                        source="semantic",
                        paper_id="DOI:10.5281/zenodo.20117466",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["source"], "zenodo")
        self.assertEqual(result["paper_id"], "10.5281/zenodo.20117466")
        self.assertEqual(result["doi"], "10.5281/zenodo.20117466")

    def test_no_scihub_returns_oa_chain_error(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=False,
                )
            )
            self.assertIn("OA fallback chain", result)

    def test_paper_fetch_fallback_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PAPER_SEARCH_MCP_PAPER_FETCH_PDF_FALLBACK": ""}):
            result = asyncio.run(
                download_engine._try_paper_fetch_download(
                    source_name="crossref",
                    paper_id="",
                    doi="10.1000/test",
                    title="test",
                    save_path=tmp,
                )
            )

        self.assertIsNone(result["path"])
        self.assertIn("disabled", result["error"])

    def test_paper_fetch_fallback_accepts_only_valid_saved_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper-fetch.pdf"

            def fake_run(query, save_path):
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf), ""

            with patch.dict("os.environ", {"PAPER_SEARCH_MCP_PAPER_FETCH_PDF_FALLBACK": "true"}), patch(
                "paper_search_mcp.engine.download._run_paper_fetch_pdf",
                side_effect=fake_run,
            ):
                result = asyncio.run(
                    download_engine._try_paper_fetch_download(
                        source_name="crossref",
                        paper_id="",
                        doi="10.1000/test",
                        title="test",
                        save_path=tmp,
                    )
                )

        self.assertEqual(result["path"], str(pdf))
        self.assertEqual(result["downloader"], "paper_fetch")

    def test_publisher_direct_resolves_known_oa_doi_prefix(self):
        url = resolve_publisher_direct_url("10.48550/arXiv.1706.03762")

        self.assertEqual(url, "https://arxiv.org/pdf/1706.03762")

    def test_engine_sequential_strategy_uses_ordered_methods(self):
        calls = []

        async def fake_attempt(*, method, **kwargs):
            calls.append(method)
            if method == "repositories":
                return {
                    "method": method,
                    "path": str(pdf),
                    "downloader": "repository_fallback",
                }
            return {"method": method, "path": None, "error": f"{method} failed"}

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "repo.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            with patch(
                "paper_search_mcp.engine.download._attempt_download_method",
                new=AsyncMock(side_effect=fake_attempt),
            ), patch.dict(
                "os.environ",
                {"PAPER_SEARCH_MCP_DOWNLOAD_STRATEGY": "sequential", "PAPER_SEARCH_MCP_LIBGEN_ENABLED": "false"},
            ):
                result, errors = asyncio.run(
                    download_engine._race_oa_downloads(
                        source_name="arxiv",
                        paper_id="1706.03762",
                        doi="",
                        title="Attention Is All You Need",
                        save_path=tmp,
                        searchers={},
                    )
                )

        self.assertEqual(result["path"], str(pdf))
        self.assertEqual(calls, ["primary", "repositories"])
        self.assertEqual(errors, ["primary: primary failed"])


class TestRepositoryFallbackNumericPaperId(unittest.TestCase):
    """Regression test for issue #57: _try_repository_fallback crashed when a
    repository connector returned a Paper whose paper_id was a non-string
    (int) value, because the code called .strip() on it directly."""

    def test_numeric_paper_id_does_not_crash(self):
        class FakePaper:
            pdf_url = "https://example.org/oa.pdf"
            title = "some title"
            doi = ""
            url = ""
            paper_id = 12345  # int, not str — caused 'int' object has no attribute 'strip'

        fake_searcher = type(
            "S", (), {"search": staticmethod(lambda q, max_results=3: [FakePaper()])}
        )

        # Patch one of the repository searchers to return our FakePaper.
        with patch.object(server, "openaire_searcher", fake_searcher), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/ok.pdf")):
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="some title",
                    save_path="/tmp",
                )
            )
            self.assertEqual(result, "/tmp/ok.pdf")
            self.assertEqual(err, "")

    def test_repository_fallback_rejects_mismatched_title(self):
        class FakePaper:
            pdf_url = "https://example.org/wrong.pdf"
            paper_id = "PMID:1"
            title = "A Completely Different Biomedical Article"
            doi = ""
            url = ""

        fake_searcher = type("S", (), {"search": staticmethod(lambda q, max_results=3: [FakePaper()])})

        with patch.object(server, "openaire_searcher", fake_searcher), patch.object(
            server, "core_searcher", fake_searcher
        ), patch.object(server, "europepmc_searcher", fake_searcher), patch.object(
            server, "pmc_searcher", fake_searcher
        ), patch(
            "paper_search_mcp.server._download_from_url",
            new=AsyncMock(side_effect=AssertionError("mismatched repository result should not download")),
        ):
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="",
                    title="SkillCraft: Can LLM Agents Learn to Use Tools Skillfully?",
                    save_path="/tmp",
                )
            )

        self.assertIsNone(result)
        self.assertIn("did not match", err)


if __name__ == "__main__":
    unittest.main()
