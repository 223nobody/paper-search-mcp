import tempfile
import unittest
from pathlib import Path

from paper_search_mcp import cache


class TestPaperCache(unittest.TestCase):
    def test_paper_key_prefers_doi(self):
        key = cache.paper_key(doi="10.1000/Test DOI", source="arxiv", paper_id="1234")
        self.assertTrue(key.startswith("doi_10.1000_test_doi"))

    def test_record_and_list_parsed_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            metadata = cache.record_download(
                pdf_path=str(pdf),
                source="arxiv",
                paper_id="1234.5678",
                title="A Test Paper",
                cache_dir=tmp,
            )

            entries = cache.list_parsed(cache_dir=tmp)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["paper_key"], metadata["paper_key"])
            self.assertEqual(entries[0]["source"], "arxiv")

    def test_delete_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = cache.paper_dir("test-paper", cache_dir=tmp)
            self.assertTrue(directory.exists())
            self.assertTrue(cache.delete_cache("test-paper", cache_dir=tmp))
            self.assertFalse(directory.exists())

    def test_search_parsed_content_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = cache.get_cached_paths("parsed-paper", cache_dir=tmp)
            cache.write_json(
                paths["content_list"],
                [
                    {"id": "b1", "type": "paragraph", "text": "This paper studies transformer attention."},
                    {"id": "b2", "type": "paragraph", "text": "Unrelated text."},
                ],
            )
            hits = cache.search_parsed("parsed-paper", "attention", cache_dir=tmp)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["block_id"], "b1")

    def test_search_session_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="agentic skills",
                sources="arxiv,semantic",
                papers=[{"title": "Skill Paper", "source": "arxiv", "paper_id": "1234.5678"}],
                metadata={"interaction": "backend_session_numbered_selection"},
                cache_dir=tmp,
            )

            self.assertTrue(session["selection_token"].startswith("search_"))
            loaded = cache.get_search_session(session["selection_token"], cache_dir=tmp)
            self.assertEqual(loaded["query"], "agentic skills")
            self.assertEqual(loaded["papers"][0]["title"], "Skill Paper")

            sessions = cache.list_search_sessions(cache_dir=tmp)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["total"], 1)

            self.assertTrue(cache.delete_search_session(session["selection_token"], cache_dir=tmp))
            self.assertEqual(cache.get_search_session(session["selection_token"], cache_dir=tmp), {})


if __name__ == "__main__":
    unittest.main()
