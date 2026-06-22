# paper_search_mcp/sources/arxiv.py
from typing import List
from datetime import datetime
import requests
import feedparser
import time
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource
from pypdf import PdfReader
import os
from pathlib import Path

class ArxivSearcher(PaperSource):
    """Searcher for arXiv papers"""
    BASE_URL = "http://export.arxiv.org/api/query"

    @staticmethod
    def _is_valid_pdf_file(path: str) -> bool:
        try:
            target = Path(path)
            if not target.exists() or not target.is_file():
                return False
            with target.open("rb") as file_obj:
                return file_obj.read(4096).lstrip().startswith(b"%PDF")
        except OSError:
            return False

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (mailto:openags@example.com)',
            'Accept': 'application/atom+xml, application/xml;q=0.9, */*;q=0.8',
        })

    @staticmethod
    def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
        raw = get_env(name, str(default)).strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(minimum, value)

    @staticmethod
    def _pdf_url_candidates(paper_id: str) -> List[str]:
        normalized = str(paper_id or "").strip().replace("arXiv:", "")
        normalized = normalized.rsplit("/", 1)[-1]
        if normalized.lower().endswith(".pdf"):
            normalized = normalized[:-4]
        base_id = normalized.split("v")[0] if "v" in normalized else normalized
        candidates = [
            f"https://arxiv.org/pdf/{normalized}",
            f"https://arxiv.org/pdf/{normalized}.pdf",
        ]
        if base_id and base_id != normalized:
            candidates.extend([
                f"https://arxiv.org/pdf/{base_id}",
                f"https://arxiv.org/pdf/{base_id}.pdf",
            ])
        return list(dict.fromkeys(candidates))

    def search(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = 'relevance',
        sort_order: str = 'descending',
        timeout: float = 12,
        max_attempts: int = 3,
    ) -> List[Paper]:
        params = {
            'search_query': f'all:{query}',
            'max_results': max_results,
            'sortBy': sort_by,
            'sortOrder': sort_order,
        }
        response = None
        for attempt in range(max(1, int(max_attempts or 1))):
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=timeout)
            except requests.RequestException:
                time.sleep((attempt + 1) * 1.5)
                continue
            if response.status_code == 200:
                break
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep((attempt + 1) * 1.5)
                continue
            break

        if response is None or response.status_code != 200:
            return []

        feed = feedparser.parse(response.content)
        papers = []
        for entry in feed.entries:
            try:
                papers.append(self._paper_from_entry(entry))
            except Exception as e:
                print(f"Error parsing arXiv entry: {e}")
        return papers

    def _paper_from_entry(self, entry) -> Paper:
        authors = [author.name for author in entry.authors]
        published = datetime.strptime(entry.published, '%Y-%m-%dT%H:%M:%SZ')
        updated = datetime.strptime(entry.updated, '%Y-%m-%dT%H:%M:%SZ')
        pdf_url = next((link.href for link in entry.links if link.type == 'application/pdf'), '')

        # Try to extract DOI from entry.doi or links or summary
        doi = entry.get('doi', '') or extract_doi(entry.summary) or extract_doi(entry.id)
        for link in entry.links:
            if link.get('title') == 'doi':
                doi = doi or extract_doi(link.href)

        paper_id = entry.id.split('/')[-1]
        # If no DOI was found, synthesize the canonical arXiv DOI for cross-source dedup
        if not doi and paper_id:
            doi = f"10.48550/arXiv.{paper_id}"

        primary_category = entry.get("arxiv_primary_category", {}).get("term", "")
        journal_ref = entry.get("arxiv_journal_ref", "") or entry.get("journal_ref", "")

        return Paper(
            paper_id=paper_id,
            title=entry.title,
            authors=authors,
            abstract=entry.summary,
            url=entry.id,
            pdf_url=pdf_url,
            published_date=published,
            updated_date=updated,
            source='arxiv',
            categories=[tag.term for tag in entry.tags],
            keywords=[],
            doi=doi,
            extra={
                "primary_category": primary_category,
                "journal_ref": journal_ref,
            }
        )

    def get_by_id(self, paper_id: str, timeout: float = 12, max_attempts: int = 3) -> Paper | None:
        params = {"id_list": paper_id, "max_results": 1}
        response = None
        for attempt in range(max(1, int(max_attempts or 1))):
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=timeout)
            except requests.RequestException:
                time.sleep((attempt + 1) * 1.5)
                continue
            if response.status_code == 200:
                break
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep((attempt + 1) * 1.5)
                continue
            break

        if response is None or response.status_code != 200:
            return None
        feed = feedparser.parse(response.content)
        if not feed.entries:
            return None
        return self._paper_from_entry(feed.entries[0])

    def download_pdf(self, paper_id: str, save_path: str, timeout: float = 30) -> str:
        os.makedirs(save_path, exist_ok=True)
        output_file = str(Path(save_path) / f"{paper_id}.pdf")
        if self._is_valid_pdf_file(output_file):
            return output_file

        temp_file = f"{output_file}.tmp"
        headers = {"Accept": "application/pdf,*/*;q=0.8"}
        max_attempts = self._env_int("ARXIV_MAX_ATTEMPTS", 2, minimum=1)
        last_error: Exception | None = None
        try:
            for attempt in range(max_attempts):
                for pdf_url in self._pdf_url_candidates(paper_id):
                    try:
                        with self.session.get(pdf_url, timeout=timeout, stream=True, headers=headers) as response:
                            response.raise_for_status()
                            with open(temp_file, 'wb') as f:
                                for chunk in response.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        f.write(chunk)

                        if not self._is_valid_pdf_file(temp_file):
                            raise ValueError(f"Downloaded arXiv file is not a valid PDF: {paper_id}")
                        os.replace(temp_file, output_file)
                        return output_file
                    except (requests.RequestException, ValueError) as exc:
                        last_error = exc
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                        continue
                if attempt + 1 < max_attempts:
                    time.sleep((attempt + 1) * 1.5)
            if last_error:
                raise last_error
            raise RuntimeError(f"arXiv PDF download failed: {paper_id}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read a paper and convert it to text format.
        
        Args:
            paper_id: arXiv paper ID
            save_path: Directory where the PDF is/will be saved
            
        Returns:
            str: The extracted text content of the paper
        """
        # First ensure we have the PDF
        pdf_path = f"{save_path}/{paper_id}.pdf"
        if not os.path.exists(pdf_path):
            pdf_path = self.download_pdf(paper_id, save_path)
        
        # Read the PDF
        try:
            reader = PdfReader(pdf_path)
            text = ""
            
            # Extract text from each page
            for page in reader.pages:
                text += page.extract_text() + "\n"
            
            return text.strip()
        except Exception as e:
            print(f"Error reading PDF for paper {paper_id}: {e}")
            return ""

if __name__ == "__main__":
    # 测试 ArxivSearcher 的功能
    searcher = ArxivSearcher()
    
    # 测试搜索功能
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
    except Exception as e:
        print(f"Error during search: {e}")
    
    # 测试 PDF 下载功能
    if papers:
        print("\nTesting PDF download functionality...")
        paper_id = papers[0].paper_id
        save_path = "./downloads"  # 确保此目录存在
        try:
            os.makedirs(save_path, exist_ok=True)
            pdf_path = searcher.download_pdf(paper_id, save_path)
            print(f"PDF downloaded successfully: {pdf_path}")
        except Exception as e:
            print(f"Error during PDF download: {e}")

    # 测试论文阅读功能
    if papers:
        print("\nTesting paper reading functionality...")
        paper_id = papers[0].paper_id
        try:
            text_content = searcher.read_paper(paper_id)
            print(f"\nFirst 500 characters of the paper content:")
            print(text_content[:500] + "...")
            print(f"\nTotal length of extracted text: {len(text_content)} characters")
        except Exception as e:
            print(f"Error during paper reading: {e}")
