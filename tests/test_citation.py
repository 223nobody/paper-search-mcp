import unittest

from paper_search_mcp.citation import export_citation, to_bibtex, to_endnote, to_ris


class TestCitationExport(unittest.TestCase):
    def setUp(self):
        self.paper = {
            "title": "Attention Is All You Need",
            "authors": "Ashish Vaswani; Noam Shazeer",
            "published_date": "2017-06-12",
            "doi": "10.5555/3295222.3295349",
            "publication_venue": "Conference on Neural Information Processing Systems",
            "url": "https://example.org/paper",
            "extra": {"pages": "5998-6008", "publisher": "Curran Associates"},
        }

    def test_bibtex_infers_conference_entry(self):
        bibtex = to_bibtex(self.paper)

        self.assertIn("@inproceedings{vaswani2017attention,", bibtex)
        self.assertIn("author = {Ashish Vaswani and Noam Shazeer}", bibtex)
        self.assertIn("booktitle = {Conference on Neural Information Processing Systems}", bibtex)
        self.assertIn("pages = {5998-6008}", bibtex)

    def test_ris_and_endnote_exports(self):
        self.assertIn("TY  - CPAPER", to_ris(self.paper))
        self.assertIn("%0 Conference Paper", to_endnote(self.paper))

    def test_export_citation_rejects_unknown_format(self):
        with self.assertRaises(ValueError):
            export_citation(self.paper, "unknown")


if __name__ == "__main__":
    unittest.main()
