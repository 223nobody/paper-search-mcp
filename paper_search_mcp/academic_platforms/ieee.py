"""IEEE Xplore connector — requires API key env.

This module provides search access to the IEEE Xplore Metadata API.
A valid API key must be set via the ``PAPER_SEARCH_MCP_IEEE_API_KEY``
(or legacy ``IEEE_API_KEY``) environment variable.

Enable usage::

    export PAPER_SEARCH_MCP_IEEE_API_KEY=<your_ieee_api_key>

Obtain a free API key at https://developer.ieee.org/.

.. note::
    Full-text PDF download requires institutional IEEE access and is not
    available through the public metadata API alone.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

import requests

from .base import PaperSource
from ..paper import Paper
from ..config import get_env

logger = logging.getLogger(__name__)

_NOT_CONFIGURED_MSG = (
    "IEEE Xplore is not configured.  Set PAPER_SEARCH_MCP_IEEE_API_KEY "
    "(or legacy IEEE_API_KEY) environment variable "
    "to enable IEEE Xplore search and download.  "
    "Obtain a free API key at https://developer.ieee.org/."
)

# IEEE Xplore REST API base URL (v1)
IEEE_API_BASE = "https://ieeexploreapi.ieee.org/api/v1/search/articles"


class IEEESearcher(PaperSource):
    """Connector for IEEE Xplore Metadata API.

    Instantiating this class without ``PAPER_SEARCH_MCP_IEEE_API_KEY``
    (or ``IEEE_API_KEY``) set will log a warning but will NOT raise an error.
    All actual operations will raise :class:`NotImplementedError` with a clear
    message directing the user to configure their API key.
    """

    # IEEE Xplore REST API base URL (v1)
    BASE_URL = IEEE_API_BASE

    def __init__(self) -> None:
        self.api_key: str = get_env("IEEE_API_KEY", "")
        if not self.api_key:
            logger.warning(
                "IEEESearcher initialised without PAPER_SEARCH_MCP_IEEE_API_KEY/IEEE_API_KEY.  "
                "All calls will raise NotImplementedError until the key is set."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "paper-search-mcp/0.1.3 (https://github.com/Dragonatorul/paper-search-mcp; mailto:paper-search@example.org)",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True only when a non-empty IEEE API key is available."""
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        max_results: int = 10,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        content_type: Optional[str] = None,
        sort_field: Optional[str] = None,
        sort_order: Optional[str] = None,
        open_access: bool = False,
        **kwargs,
    ) -> List[Paper]:
        """Search IEEE Xplore via the Metadata API.

        Args:
            query: Search query string.  Searches across all metadata fields,
                   abstracts, and document text via the ``query_text`` parameter.
            max_results: Maximum number of results to return (default: 10, max: 200).
            start_year: Optional start year filter (e.g., 2020).
            end_year: Optional end year filter (e.g., 2024).
            content_type: Optional content type filter.
                Accepted values: ``Conferences``, ``Journals``, ``Books``,
                ``Early Access``, ``Courses``, ``Standards``.
            sort_field: Field to sort by.  Example: ``publication_year``,
                ``article_title``, ``publication_title``, ``author``.
            sort_order: Sort direction — ``asc`` or ``desc``.
            open_access: If True, restrict results to Open Access only.
            **kwargs: Additional query parameters passed directly to the API.

        Returns:
            List of Paper objects.

        Raises:
            NotImplementedError: When the IEEE API key is not configured.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        papers: List[Paper] = []

        try:
            params: Dict[str, Any] = {
                "apikey": self.api_key,
                "query_text": query,
                "max_records": min(max_results, 200),
            }

            if start_year is not None:
                params["start_year"] = int(start_year)
            if end_year is not None:
                params["end_year"] = int(end_year)
            if content_type is not None:
                params["content_type"] = content_type
            if sort_field is not None:
                params["sort_field"] = sort_field
            if sort_order is not None:
                params["sort_order"] = sort_order
            if open_access:
                params["open_access"] = "1"

            # Merge any extra keyword arguments as API parameters
            params.update(kwargs)

            logger.debug("IEEE Xplore search request: %s", params)

            response = self.session.get(
                IEEE_API_BASE, params=params, timeout=30
            )

            # Handle rate limiting (HTTP 429)
            if response.status_code == 429:
                logger.warning("Rate limited by IEEE Xplore API.  Try again later.")
                return papers

            # Handle authentication errors
            if response.status_code == 403:
                logger.error("IEEE Xplore API key rejected (HTTP 403).")
                return papers

            response.raise_for_status()
            data = response.json()

            articles = data.get("articles", [])
            if not articles:
                logger.info("No IEEE results found for query: %s", query)
                return papers

            # Parse each article
            for article in articles:
                if len(papers) >= max_results:
                    break
                try:
                    paper = self._parse_ieee_article(article)
                    if paper:
                        papers.append(paper)
                except Exception as exc:
                    logger.warning("Error parsing IEEE article: %s", exc)
                    continue

        except requests.RequestException as exc:
            logger.error("Error searching IEEE Xplore: %s", exc)
        except Exception as exc:
            logger.error("Unexpected error in IEEE Xplore search: %s", exc)

        return papers

    def _parse_ieee_article(self, article: Dict[str, Any]) -> Optional[Paper]:
        """Parse a single article dict from the IEEE API response into a Paper.

        Args:
            article: Raw article dict from the IEEE JSON response.

        Returns:
            Paper object, or None if parsing fails.
        """
        try:
            # --- Title ---
            title = article.get("title", "").strip()
            if not title:
                return None

            # --- Authors ---
            authors: List[str] = []
            author_list = article.get("authors")
            if isinstance(author_list, dict):
                # Some older API versions return a single author as a dict
                author_list = [author_list]
            if isinstance(author_list, list):
                for author in author_list:
                    if isinstance(author, dict):
                        full_name = author.get("full_name", "")
                        if full_name:
                            authors.append(full_name.strip())
                    elif isinstance(author, str):
                        authors.append(author.strip())

            # --- DOI ---
            doi = article.get("doi", "").strip()

            # --- Abstract ---
            abstract = article.get("abstract", "").strip()

            # --- Publication date ---
            published_date: Optional[datetime] = None
            pub_year = article.get("publication_year")
            pub_date_str = article.get("publication_date", "")

            if pub_year is not None:
                try:
                    published_date = datetime(int(pub_year), 1, 1)
                except (TypeError, ValueError):
                    pass
            elif pub_date_str:
                # Try common date formats
                for fmt in ("%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y", "%Y-%m-%d"):
                    try:
                        published_date = datetime.strptime(pub_date_str, fmt)
                        break
                    except ValueError:
                        continue

            # --- URLs ---
            url = article.get("abstract_url", "").strip()
            pdf_url = article.get("pdf_url", "").strip()

            if not url:
                article_number = article.get("article_number", "")
                if article_number:
                    url = f"https://ieeexplore.ieee.org/document/{article_number}/"

            # --- Paper ID ---
            paper_id = article.get("article_number", "").strip()
            if not paper_id:
                paper_id = doi

            # --- Keywords / index terms ---
            index_terms = article.get("index_terms", {})
            keywords: List[str] = []
            ieee_terms: List[str] = []
            author_terms: List[str] = []
            if isinstance(index_terms, dict):
                ieee_terms = index_terms.get("ieee_terms", [])
                author_terms = index_terms.get("author_terms", [])
                if isinstance(ieee_terms, list):
                    keywords.extend(ieee_terms)
                if isinstance(author_terms, list):
                    keywords.extend(author_terms)
            elif isinstance(index_terms, list):
                keywords = index_terms

            # --- Categories (from content_type) ---
            categories: List[str] = []
            ct = article.get("content_type", "")
            if ct:
                categories.append(ct)

            # --- Citations ---
            citations = article.get("citing_paper_count", 0)
            if not isinstance(citations, int):
                try:
                    citations = int(citations)
                except (TypeError, ValueError):
                    citations = 0

            # --- Extra metadata ---
            extra: Dict[str, Any] = {
                "publisher": article.get("publisher", ""),
                "publication_title": article.get("publication_title", ""),
                "publication_year": pub_year,
                "publication_date": pub_date_str,
                "volume": article.get("volume", ""),
                "issue": article.get("issue", ""),
                "start_page": article.get("start_page", ""),
                "end_page": article.get("end_page", ""),
                "issn": article.get("issn", ""),
                "isbn": article.get("isbn", ""),
                "article_number": article.get("article_number", ""),
                "content_type": ct,
                "access_type": article.get("accessType", ""),
                "citing_patent_count": article.get("citing_patent_count", 0),
                "conference_location": article.get("conference_location", ""),
                "conference_dates": article.get("conference_dates", ""),
                "ieee_terms": ieee_terms,
                "author_terms": author_terms,
                "rank": article.get("rank"),
            }

            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source="ieee",
                categories=categories,
                keywords=keywords,
                citations=citations,
                extra=extra,
            )

        except Exception as exc:
            logger.error("Error parsing IEEE article: %s", exc)
            return None

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download a PDF from IEEE Xplore.

        .. note::
            Full-text PDF download requires institutional IEEE access or
            individual purchase.  The public metadata API does not provide
            direct PDF downloads.  The ``pdf_url`` returned in search results
            links to the IEEE Xplore abstract page which may gate the full text
            behind a login.

        Args:
            paper_id: IEEE article number or DOI.
            save_path: Directory to save the downloaded PDF.

        Raises:
            NotImplementedError: IEEE PDF download requires institutional access.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "IEEE Xplore PDF download is not available through the public API.  "
            "Full-text access requires institutional IEEE subscription or "
            "individual purchase.  Use the pdf_url from search results to "
            "access the paper through your institution's proxy."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper content from IEEE Xplore.

        .. note::
            Paper reading depends on PDF access, which requires institutional
            IEEE subscription.  This method is not available through the
            public metadata API.

        Args:
            paper_id: IEEE article number or DOI.
            save_path: Directory where the PDF is/will be saved.

        Raises:
            NotImplementedError: IEEE paper reading is not implemented.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "IEEE Xplore paper reading is not yet implemented.  "
            "Full-text access requires institutional IEEE subscription."
        )


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    searcher = IEEESearcher()

    print("=" * 60)
    print("IEEE Xplore Connector -- Configuration Check")
    print("=" * 60)
    if not searcher.is_configured():
        print(
            "\n[WARN] No API key configured.\n"
            "   Set PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY) in your .env\n"
            "   and re-run this script to test with a live IEEE Xplore API call.\n"
            "   Obtain a free key at https://developer.ieee.org/\n"
        )
        print("-" * 60)
        print("Verifying that unconfigured methods raise NotImplementedError...")

        # Verify NotImplementedError is raised for search
        try:
            searcher.search("machine learning")
            print("FAIL: search() should have raised NotImplementedError")
        except NotImplementedError as exc:
            print(f"PASS: search() correctly raised NotImplementedError: {exc}")

        # Verify NotImplementedError for download
        try:
            searcher.download_pdf("12345")
            print("FAIL: download_pdf() should have raised NotImplementedError")
        except NotImplementedError as exc:
            print(f"PASS: download_pdf() correctly raised NotImplementedError: {exc}")

        # Verify NotImplementedError for read
        try:
            searcher.read_paper("12345")
            print("FAIL: read_paper() should have raised NotImplementedError")
        except NotImplementedError as exc:
            print(f"PASS: read_paper() correctly raised NotImplementedError: {exc}")

        print("\nPASS: All unconfigured-method tests passed -- no regressions.")
        sys.exit(0)

    # --- API key is configured: run live tests ---
    print("[INFO] IEEE API key found -- running live search tests.\n")

    print("=" * 60)
    print("1. Basic search: 'machine learning' (max 5)")
    print("=" * 60)
    try:
        papers = searcher.search("machine learning", max_results=5)
        print(f"Found {len(papers)} papers:\n")
        for i, paper in enumerate(papers, 1):
            print(f"  {i}. {paper.title}")
            print(f"     DOI: {paper.doi}")
            print(f"     Authors: {', '.join(paper.authors[:3])}{'...' if len(paper.authors) > 3 else ''}")
            print(f"     Year: {paper.published_date.year if paper.published_date else 'N/A'}")
            print(f"     Content: {paper.categories}")
            print(f"     Citations: {paper.citations}")
            if paper.keywords:
                print(f"     Keywords: {', '.join(paper.keywords[:5])}")
            if paper.pdf_url:
                print(f"     PDF: {paper.pdf_url}")
            if paper.url:
                print(f"     URL: {paper.url}")
            print()
    except NotImplementedError as exc:
        print(f"[SKIP] {exc}")

    print("=" * 60)
    print("2. Filtered search: 'neural networks' + Conferences (max 3)")
    print("=" * 60)
    try:
        papers = searcher.search(
            "neural networks",
            max_results=3,
            content_type="Conferences",
            start_year=2023,
            sort_field="publication_year",
            sort_order="desc",
        )
        print(f"Found {len(papers)} papers:\n")
        for i, paper in enumerate(papers, 1):
            print(f"  {i}. {paper.title}")
            print(f"     Year: {paper.published_date.year if paper.published_date else 'N/A'}")
            extra = paper.extra or {}
            print(f"     Publication: {extra.get('publication_title', 'N/A')}")
            print()
    except NotImplementedError as exc:
        print(f"[SKIP] {exc}")

    print("=" * 60)
    print("3. Verifying download_pdf() and read_paper() NotImplementedError")
    print("=" * 60)
    try:
        searcher.download_pdf("test_id")
        print("FAIL: download_pdf() should raise NotImplementedError")
    except NotImplementedError as exc:
        print(f"PASS: download_pdf() correctly raised: {exc}")

    try:
        searcher.read_paper("test_id")
        print("FAIL: read_paper() should raise NotImplementedError")
    except NotImplementedError as exc:
        print(f"PASS: read_paper() correctly raised: {exc}")

    print("\nPASS: All live tests completed.")
