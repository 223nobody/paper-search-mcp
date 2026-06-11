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

    def test_read_parsed_prefers_visible_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            metadata = cache.record_download(
                pdf_path=str(pdf),
                paper_key_hint="visible-paper",
                source="local",
                title="Visible Paper",
                cache_dir=tmp,
            )
            visible = cache.visible_artifact_paths(pdf)
            cache.write_json(visible["content_list"], [{"id": "v1", "text": "visible content"}])
            Path(visible["full_md"]).write_text("visible markdown", encoding="utf-8")
            cache.write_json(visible["manifest"], {"parser": "mineru", "backend": "visible"})

            cached = cache.get_cached_paths(metadata["paper_key"], cache_dir=tmp)
            cache.write_json(cached["content_list"], [{"id": "c1", "text": "cached content"}])
            Path(cached["full_md"]).write_text("cached markdown", encoding="utf-8")

            self.assertEqual(cache.read_parsed("visible-paper", "markdown", cache_dir=tmp), "visible markdown")
            self.assertEqual(cache.read_parsed("visible-paper", "json", cache_dir=tmp)[0]["id"], "v1")
            self.assertEqual(cache.read_parsed("visible-paper", "manifest", cache_dir=tmp)["backend"], "visible")
            resolved_paths = cache.read_parsed("visible-paper", "paths", cache_dir=tmp)
            self.assertEqual(resolved_paths["pdf_path"], str(pdf.resolve()))
            self.assertEqual(resolved_paths["full_md"], visible["full_md"])

    def test_cleanup_redundant_artifacts_removes_only_safe_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            metadata = cache.record_download(
                pdf_path=str(pdf),
                paper_key_hint="cleanup-paper",
                source="local",
                title="Cleanup Paper",
                cache_dir=tmp,
            )

            visible = cache.visible_artifact_paths(pdf)
            Path(visible["export_dir"]).mkdir(parents=True, exist_ok=True)
            Path(visible["full_md"]).write_text("visible markdown", encoding="utf-8")
            cache.write_json(visible["content_list"], [{"id": "v1", "text": "visible content"}])
            cache.write_json(visible["manifest"], {"parser": "mineru", "backend": "visible"})
            Path(visible["assets_dir"]).mkdir(parents=True, exist_ok=True)
            Path(visible["assets_dir"], "figure.png").write_bytes(b"visible")

            cached = cache.get_cached_paths(metadata["paper_key"], cache_dir=tmp)
            Path(cached["source_pdf"]).parent.mkdir(parents=True, exist_ok=True)
            Path(cached["source_pdf"]).write_bytes(pdf.read_bytes())
            Path(cached["mineru_dir"]).mkdir(parents=True, exist_ok=True)
            Path(cached["full_md"]).write_text("cached markdown", encoding="utf-8")
            cache.write_json(cached["content_list"], [{"id": "c1", "text": "cached content"}])
            cache.write_json(cached["manifest"], {"parser": "mineru", "backend": "cached"})
            Path(cached["assets_dir"]).mkdir(parents=True, exist_ok=True)
            Path(cached["assets_dir"], "figure.png").write_bytes(b"cached")
            raw_dir = Path(cached["mineru_dir"]) / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            Path(raw_dir, "origin.pdf").write_bytes(b"raw")

            dry_run = cache.cleanup_redundant_artifacts(cache_dir=tmp)
            self.assertEqual(dry_run["status"], "dry_run")
            self.assertGreaterEqual(dry_run["removed_total"], 5)
            self.assertTrue(Path(cached["source_pdf"]).exists())
            self.assertTrue(raw_dir.exists())

            applied = cache.cleanup_redundant_artifacts(cache_dir=tmp, dry_run=False)
            self.assertEqual(applied["status"], "ok")
            self.assertGreater(applied["bytes_deleted"], 0)
            self.assertFalse(Path(cached["source_pdf"]).exists())
            self.assertFalse(Path(cached["full_md"]).exists())
            self.assertFalse(Path(cached["content_list"]).exists())
            self.assertFalse(Path(cached["assets_dir"]).exists())
            self.assertFalse(raw_dir.exists())
            self.assertTrue(Path(visible["full_md"]).exists())
            self.assertTrue(Path(visible["content_list"]).exists())

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
