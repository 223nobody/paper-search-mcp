# tests/test_server.py
import unittest
import asyncio
import os
import tempfile
from pathlib import Path
from pypdf import PdfWriter
from unittest.mock import patch
from paper_search_mcp import server

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
        parsed = server._parse_sources("dblp,doaj,base,zenodo,hal,ssrn,unpaywall,invalid")
        self.assertEqual(parsed, ["dblp", "doaj", "base", "zenodo", "hal", "ssrn", "unpaywall"])

    def test_list_sources_exposes_capabilities(self):
        result = asyncio.run(server.list_sources())
        self.assertIn("sources", result)
        arxiv = next(source for source in result["sources"] if source["name"] == "arxiv")
        self.assertTrue(arxiv["search"])
        self.assertTrue(arxiv["download"])

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

        # 下载目录
        save_path = "./downloads"
        os.makedirs(save_path, exist_ok=True)  # 确保目录存在

        # 下载每个搜索结果的 PDF
        for paper in search_results:
            paper_id = paper['paper_id']
            result = asyncio.run(server.download_arxiv(paper_id, save_path))
            self.assertIsInstance(result, dict, f"Result for {paper_id} should include download metadata")
            self.assertEqual(result["status"], "downloaded")
            self.assertTrue(result["pdf_path"].endswith(".pdf"), f"Result for {paper_id} should be a PDF file path")
            self.assertTrue(os.path.exists(result["pdf_path"]), f"PDF file for {paper_id} should exist on disk")
            self.assertEqual(result["parse_prompt"]["interaction"], "backend_session_numbered_selection")

if __name__ == "__main__":
    unittest.main()
