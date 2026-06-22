import unittest
import os

from tests.helpers import check_api_accessible

BIO_API_CHECK_URL = "https://api.biorxiv.org/details/biorxiv/0/1"


class _BioRxivBaseTest:
    """Shared tests for bioRxiv/medRxiv search implementations."""

    searcher_cls = None          # set by subclasses
    source_label = "biorxiv"     # set by subclasses

    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible(BIO_API_CHECK_URL, timeout=5)
        if not cls.api_accessible:
            print(f"\nWarning: {cls.source_label} API is not accessible, some tests will be skipped")

    def setUp(self):
        self.searcher = self.searcher_cls()

    def test_search(self):
        if not self.api_accessible:
            self.skipTest(f"{self.source_label} API is not accessible")

        papers = self.searcher.search("machine learning", max_results=10)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
        self.assertGreater(len(papers), 0)
        self.assertTrue(papers[0].title)

    def test_download_and_read(self):
        if not self.api_accessible:
            self.skipTest(f"{self.source_label} API is not accessible")

        papers = self.searcher.search("machine learning", max_results=1)
        if not papers:
            self.skipTest("No papers found for testing download")

        save_path = "./downloads"
        os.makedirs(save_path, exist_ok=True)
        paper = papers[0]
        pdf_path = None

        try:
            pdf_path = self.searcher.download_pdf(paper.paper_id, save_path)
            self.assertTrue(os.path.exists(pdf_path))

            text_content = self.searcher.read_paper(paper.paper_id, save_path)
            self.assertGreater(len(text_content), 0)
        finally:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
            if os.path.exists(save_path):
                os.rmdir(save_path)


class TestBioRxivSearcher(unittest.TestCase, _BioRxivBaseTest):
    """Tests for bioRxiv searcher."""

    @classmethod
    def setUpClass(cls):
        from paper_search_mcp.academic_platforms.biorxiv import BioRxivSearcher
        cls.searcher_cls = BioRxivSearcher
        cls.source_label = "biorxiv"
        _BioRxivBaseTest.setUpClass.__func__(cls)


class TestMedRxivSearcher(unittest.TestCase, _BioRxivBaseTest):
    """Tests for medRxiv searcher."""

    @classmethod
    def setUpClass(cls):
        from paper_search_mcp.academic_platforms.medrxiv import MedRxivSearcher
        cls.searcher_cls = MedRxivSearcher
        cls.source_label = "medrxiv"
        _BioRxivBaseTest.setUpClass.__func__(cls)


if __name__ == '__main__':
    unittest.main()
