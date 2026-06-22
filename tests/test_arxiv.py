# tests/test_arxiv.py
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher

class FakeResponse:
    def __init__(self, status_code=200, chunks=None):
        self.status_code = status_code
        self._chunks = chunks or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        yield from self._chunks


class TestArxivSearcher(unittest.TestCase):
    def test_search(self):
        searcher = ArxivSearcher()
        papers = searcher.search("machine learning", max_results=10)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
        if not papers:
            self.skipTest("arXiv API is unavailable or rate-limited")
        self.assertGreater(len(papers), 0)
        self.assertTrue(papers[0].title)

    def test_download_pdf_reuses_existing_valid_pdf(self):
        searcher = ArxivSearcher()
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "2601.00001.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            with patch.object(searcher.session, "get", side_effect=AssertionError("network should not be called")):
                result = searcher.download_pdf("2601.00001", tmp)

        self.assertEqual(result, str(pdf))

    def test_download_pdf_tries_canonical_url_before_pdf_suffix(self):
        searcher = ArxivSearcher()
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if url.endswith(".pdf"):
                raise AssertionError("canonical arXiv PDF URL should be attempted first")
            return FakeResponse(chunks=[b"%PDF-1.4\n", b"%%EOF"])

        with tempfile.TemporaryDirectory() as tmp, patch.object(searcher.session, "get", side_effect=fake_get):
            result = searcher.download_pdf("2601.00001", tmp, timeout=1)

        self.assertEqual(result, str(Path(tmp) / "2601.00001.pdf"))
        self.assertEqual(calls[0], "https://arxiv.org/pdf/2601.00001")

    def test_download_pdf_falls_back_to_pdf_suffix_url(self):
        searcher = ArxivSearcher()
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if not url.endswith(".pdf"):
                return FakeResponse(chunks=[b"<html>not pdf</html>"])
            return FakeResponse(chunks=[b"%PDF-1.4\n", b"%%EOF"])

        with tempfile.TemporaryDirectory() as tmp, patch.object(searcher.session, "get", side_effect=fake_get):
            result = searcher.download_pdf("2601.00001", tmp, timeout=1)

        self.assertEqual(result, str(Path(tmp) / "2601.00001.pdf"))
        self.assertEqual(
            calls[:2],
            [
                "https://arxiv.org/pdf/2601.00001",
                "https://arxiv.org/pdf/2601.00001.pdf",
            ],
        )

if __name__ == '__main__':
    unittest.main()
