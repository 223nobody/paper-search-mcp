# paper_search_mcp/engine/paper.py
"""
Paper value normalisation, scoring, deduplication, ranking, and parse-candidate
construction.

Extracted from server.py.  This module has **zero** MCP / FastMCP dependencies.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_env
from ..utils import extract_doi
from .search import SOURCE_CAPABILITIES, _source_reliability_score

# ---------------------------------------------------------------------------
# Environment helpers (local copy to avoid circular imports with server.py)
# ---------------------------------------------------------------------------
PREFER_ARXIV_ENV = "PREFER_ARXIV"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = get_env(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# =========================================================================
# 1.  Basic value helpers
# =========================================================================


def _paper_value(value: Any) -> str:
    """Convert any paper field value to a printable string."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if item is not None)
    return str(value)


def _normalize_lookup_text(value: Any) -> str:
    """Collapse whitespace and lowercase for fuzzy-lookup / dedup keys."""
    return re.sub(r"\s+", " ", _paper_value(value).strip().lower())


def _paper_field(paper: Dict[str, Any], field: str) -> str:
    """Safely read a single field from a paper dict."""
    return str(paper.get(field) or "").strip()


def _paper_extra_value(paper: Dict[str, Any], *fields: str) -> str:
    """Return the first non-empty value from ``paper['extra']`` for *fields."""
    extra = paper.get("extra")
    if not isinstance(extra, dict):
        return ""
    for field in fields:
        value = extra.get(field)
        if value not in (None, "", [], {}):
            return _paper_value(value).strip()
    return ""


def _token_set(value: Any) -> set[str]:
    """Tokenise a string into lowercase alphanumeric tokens of length > 2."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _paper_value(value).lower())
        if len(token) > 2
    }


# =========================================================================
# 2.  File / path helpers
# =========================================================================


def _safe_filename(filename_hint: str, default: str = "paper") -> str:
    """Sanitise a string into a safe filename stem."""
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename_hint).strip("._")
    if not safe:
        return default
    return safe[:120]


def _pdf_filename_from_hint(filename_hint: str, default: str = "paper") -> str:
    """Turn a filename hint into a .pdf filename, preserving an existing .pdf."""
    stem = _safe_filename(filename_hint, default=default)
    if stem.lower().endswith(".pdf"):
        return stem
    return f"{stem}.pdf"


def _looks_like_pdf_path(value: Any) -> bool:
    """Return True when *value* is a plausible local PDF path."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "\n" in text or "\r" in text:
        return False
    if os.path.exists(text):
        return True
    return text.lower().endswith(".pdf")


def _looks_like_direct_pdf_url(value: Any) -> bool:
    """Return True when a URL is very likely to be a direct PDF route."""
    text = _paper_value(value).strip().lower()
    if not text:
        return False
    if text.endswith(".pdf") or "/pdf/" in text or "type=printable" in text:
        return True
    return any(
        marker in text
        for marker in (
            "arxiv.org/pdf/",
            "/content/",
            "/article/file",
            "/download/",
        )
    )


def _download_route_for_candidate(
    *,
    source: str,
    paper_id: str,
    doi: str,
    title: str,
    pdf_url: str,
    local_pdf_path: str,
    arxiv_id: str,
) -> tuple[bool, str, str]:
    """Classify how confidently a candidate can be downloaded."""
    download_capability = SOURCE_CAPABILITIES.get(source, {}).get("download")
    has_source_download = bool(
        source and paper_id and download_capability not in {False, None}
    )

    if local_pdf_path:
        return True, "local_pdf_path", "high"
    if arxiv_id:
        return True, "arxiv_pdf", "high"
    if pdf_url and _looks_like_direct_pdf_url(pdf_url):
        return True, "direct_pdf_url", "high"
    if pdf_url:
        return False, "unverified_pdf_url", "low"
    if has_source_download:
        return True, "source_native_download", "medium"
    if doi:
        return False, "doi_no_verified_route", "low"
    if title and has_source_download:
        return True, "title_repository_fallback", "medium"
    if title:
        return False, "title_no_verified_route", "low"
    return False, "missing_pdf_url_source_id_doi_title", "low"


# =========================================================================
# 3.  Paper identity and basic metadata extraction
# =========================================================================


def _paper_arxiv_id(paper: Dict[str, Any]) -> str:
    """Extract an arXiv paper ID from all relevant fields, including extra."""
    # Check explicit field first
    extra = paper.get("extra")
    if isinstance(extra, dict):
        arxiv_from_extra = _paper_value(extra.get("arxiv_id")).strip()
        if arxiv_from_extra:
            return arxiv_from_extra

    # Check paper_id, url, pdf_url, doi via the existing regex-based extractor.
    # Strip .pdf suffix from URL-like fields first so the regex can match
    # arXiv IDs inside paths like /pdf/1706.03762.pdf
    def _clean_for_arxiv_extract(value: Any) -> str:
        text = _paper_value(value).strip()
        if text.lower().endswith(".pdf"):
            text = text[:-4]
        return text

    arxiv_id = _extract_arxiv_id(
        _clean_for_arxiv_extract(paper.get("paper_id")),
        _clean_for_arxiv_extract(paper.get("url")),
        _clean_for_arxiv_extract(paper.get("pdf_url")),
        _clean_for_arxiv_extract(paper.get("doi")),
    )
    if arxiv_id:
        return arxiv_id

    # Also check extra fields for arxiv_id explicitly
    if isinstance(extra, dict):
        for key in ("arxiv_id", "arxiv", "ArXiv"):
            value = _paper_value(extra.get(key)).strip()
            if value:
                extracted = _extract_arxiv_id(value)
                if extracted:
                    return extracted

    return ""


def _strip_arxiv_version(arxiv_id: str) -> str:
    """Remove version suffix (e.g. 'v1', 'v2') from an arXiv ID for dedup."""
    import re as _re
    return _re.sub(r"v\d+$", "", arxiv_id.strip().lower())


def _paper_unique_key(paper: Dict[str, Any]) -> str:
    """Produce a dedup key prioritising DOI > arXiv ID > title+authors > paper_id."""
    # ── Tier 1: DOI ──────────────────────────────────────────────
    doi = _normalize_lookup_text(paper.get("doi"))
    if doi:
        return f"doi:{doi}"

    # ── Tier 2: arXiv ID (cross-source golden key for arXiv papers)
    arxiv_id = _paper_arxiv_id(paper)
    if arxiv_id:
        return f"arxiv:{_strip_arxiv_version(arxiv_id)}"

    # ── Tier 3: Title + Authors ──────────────────────────────────
    title = _normalize_lookup_text(paper.get("title"))
    authors = _normalize_lookup_text(paper.get("authors"))
    if title:
        return f"title:{title}|authors:{authors}"

    # ── Tier 4: paper_id (source-specific, last resort) ──────────
    paper_id = _normalize_lookup_text(paper.get("paper_id"))
    return f"id:{paper_id}"


def _paper_year_number(paper: Dict[str, Any]) -> int:
    """Return a four-digit year integer from common fields, or 0."""
    for name in (
        "year",
        "published_year",
        "publication_year",
        "published_date",
        "date",
    ):
        match = re.search(r"(19|20)\d{2}", _paper_value(paper.get(name)))
        if match:
            return int(match.group(0))
    return 0


def _paper_has_pdf_signal(paper: Dict[str, Any]) -> bool:
    """Heuristic: does this paper record have likely PDF access?"""
    for name in ("pdf_url", "open_access_pdf", "local_pdf_path"):
        if _paper_value(paper.get(name)).strip():
            return True
    url = _paper_value(paper.get("url")).lower()
    return url.endswith(".pdf") or "/pdf" in url


def _paper_citations(paper: Dict[str, Any]) -> int:
    """Return citation count (non-negative integer) or 0."""
    for name in ("citation_count", "citations", "num_citations"):
        value = paper.get(name)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _best_source_download_capability(sources: List[str]) -> Any:
    """Return the best download capability among *sources*.

    Returns:
        ``True`` for sources that reliably serve PDFs (arxiv, biorxiv, etc.).
        A truthy string like ``"oa_pdf"`` for conditional OA PDF access.
        ``"record_dependent"`` for sources whose download varies per record.
        ``False`` / falsy for metadata-only sources (crossref, openalex, dblp).
        ``None`` if no capability data is available.
    """
    best: Any = None
    for source in sources:
        key = str(source).strip().lower()
        cap = SOURCE_CAPABILITIES.get(key, {}).get("download")
        if cap is True:
            return True  # best possible — short-circuit
        if cap == "record_dependent":
            if best is None or best is False:
                best = cap
        elif cap:
            if best is None or best is False or best == "record_dependent":
                best = cap
        else:
            if best is None:
                best = cap
    return best


def _paper_sources(paper: Dict[str, Any]) -> List[str]:
    """Return all normalized source names attached to a paper record."""
    values: List[str] = []
    raw_sources = paper.get("sources", [])
    if isinstance(raw_sources, (list, tuple, set)):
        values.extend(str(source).strip().lower() for source in raw_sources if source)
    source = _paper_value(paper.get("source")).strip().lower()
    if source:
        values.append(source)
    records = paper.get("source_records") or []
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            record_source = _paper_value(record.get("source")).strip().lower()
            if record_source:
                values.append(record_source)
    return list(dict.fromkeys(source for source in values if source))


def _paper_source_reliability_score(paper: Dict[str, Any]) -> float:
    """Return the best PDF-first reliability score from a paper's sources."""
    sources = _paper_sources(paper)
    if not sources:
        return 0.0
    return max(_source_reliability_score(source) for source in sources)


def _paper_year(paper: Dict[str, Any]) -> str:
    """Return a four-digit year *string* from common fields, or ''."""
    for field in ("year", "published_date", "publication_date", "updated_date"):
        value = _paper_field(paper, field)
        if not value:
            continue
        match = re.search(r"(19|20)\d{2}", value)
        if match:
            return match.group(0)
    return ""


# =========================================================================
# 4.  ArXiv identity helpers
# =========================================================================

ARXIV_ID_RE = re.compile(
    r"(?<![\w.])(\d{4}\.\d{4,5}(?:v\d+)?)(?![\w.])", re.IGNORECASE
)
ARXIV_DOI_RE = re.compile(
    r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE
)


def _extract_arxiv_id(*values: Any) -> str:
    """Extract the first arXiv paper id from any of *values."""
    for value in values:
        text = _paper_value(value).strip()
        if not text:
            continue
        doi_match = ARXIV_DOI_RE.search(text)
        if doi_match:
            return doi_match.group(1)
        lowered = text.lower()
        if "arxiv.org" in lowered:
            match = ARXIV_ID_RE.search(text)
            if match:
                return match.group(1)
        if ARXIV_ID_RE.fullmatch(text):
            return text
    return ""


def _canonical_pdf_stem(
    *,
    source: str = "",
    paper_id: str = "",
    doi: str = "",
    title: str = "",
    pdf_url: str = "",
    url: str = "",
    fallback: str = "paper",
) -> str:
    """Resolve the best stable filename stem for a paper's artefacts."""
    arxiv_id = _extract_arxiv_id(paper_id, doi, pdf_url, url)
    if arxiv_id:
        return arxiv_id

    normalized_doi = (doi or "").strip()
    if normalized_doi:
        return _safe_filename(normalized_doi.replace("/", "_"), default=fallback)

    identifier = (paper_id or "").strip()
    if identifier and not identifier.lower().startswith("gs_"):
        return _safe_filename(
            identifier.replace("/", "_").replace("\\", "_"), default=fallback
        )

    if title:
        return _safe_filename(title, default=fallback)
    return _safe_filename(fallback, default="paper")


# =========================================================================
# 5.  DOI / identifier normalisation
# =========================================================================


def _normalize_identifier_doi(value: str) -> str:
    """Strip 'doi:' prefix and extract a clean DOI string."""
    raw = (value or "").strip()
    if raw.lower().startswith("doi:"):
        raw = raw.split(":", 1)[1].strip()
    return extract_doi(raw)


def _source_from_identifier(
    source: str, paper_id: str, doi: str = ""
) -> tuple[str, str, str]:
    """Resolve (source_name, identifier, normalized_doi) from raw inputs."""
    source_name = (source or "").strip().lower()
    identifier = (paper_id or "").strip()
    normalized_doi = (doi or "").strip() or _normalize_identifier_doi(identifier)
    lowered = identifier.lower()

    if normalized_doi:
        if "10.5281/zenodo." in normalized_doi.lower():
            return "zenodo", normalized_doi, normalized_doi
        if "10.2139/ssrn." in normalized_doi.lower():
            return "ssrn", normalized_doi, normalized_doi

    if lowered.startswith("zenodo:") or re.search(
        r"zenodo\.\d+", identifier, re.IGNORECASE
    ):
        return "zenodo", identifier, normalized_doi
    if lowered.startswith("ssrn:") or "ssrn.com" in lowered:
        return "ssrn", identifier, normalized_doi

    return source_name, identifier, normalized_doi


def _paper_doi(paper: Dict[str, Any]) -> str:
    """Return the best DOI for *paper*, recovering from other fields if needed."""
    explicit = _paper_field(paper, "doi")
    if explicit:
        return explicit

    for field in ("paper_id", "url", "pdf_url"):
        recovered = extract_doi(_paper_field(paper, field))
        if recovered:
            return recovered
    return ""


# =========================================================================
# 6.  Publication venue constants and helpers
# =========================================================================

GENERIC_PUBLICATION_VENUES = {
    "arxiv",
    "arxiv.org",
    "arxiv preprint",
    "arxiv preprints",
    "preprint",
    "preprints",
}

ARXIV_CATEGORY_VENUES = {
    "cs.AI": "Artificial Intelligence",
    "cs.AR": "Hardware Architecture",
    "cs.CC": "Computational Complexity",
    "cs.CE": "Computational Engineering, Finance, and Science",
    "cs.CG": "Computational Geometry",
    "cs.CL": "Computation and Language",
    "cs.CR": "Cryptography and Security",
    "cs.CV": "Computer Vision and Pattern Recognition",
    "cs.CY": "Computers and Society",
    "cs.DB": "Databases",
    "cs.DC": "Distributed, Parallel, and Cluster Computing",
    "cs.DL": "Digital Libraries",
    "cs.DM": "Discrete Mathematics",
    "cs.DS": "Data Structures and Algorithms",
    "cs.ET": "Emerging Technologies",
    "cs.FL": "Formal Languages and Automata Theory",
    "cs.GL": "General Literature",
    "cs.GR": "Graphics",
    "cs.GT": "Computer Science and Game Theory",
    "cs.HC": "Human-Computer Interaction",
    "cs.IR": "Information Retrieval",
    "cs.IT": "Information Theory",
    "cs.LG": "Machine Learning",
    "cs.LO": "Logic in Computer Science",
    "cs.MA": "Multiagent Systems",
    "cs.MM": "Multimedia",
    "cs.MS": "Mathematical Software",
    "cs.NA": "Numerical Analysis",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.NI": "Networking and Internet Architecture",
    "cs.OH": "Other Computer Science",
    "cs.OS": "Operating Systems",
    "cs.PF": "Performance",
    "cs.PL": "Programming Languages",
    "cs.RO": "Robotics",
    "cs.SC": "Symbolic Computation",
    "cs.SD": "Sound",
    "cs.SE": "Software Engineering",
    "cs.SI": "Social and Information Networks",
    "cs.SY": "Systems and Control",
    "stat.ML": "Machine Learning",
    "eess.IV": "Image and Video Processing",
}


def _is_generic_publication_venue(value: str) -> bool:
    """Return True when *value* is a catch-all venue label like 'arxiv'."""
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return normalized in GENERIC_PUBLICATION_VENUES


def _arxiv_category_venue(paper: Dict[str, Any]) -> str:
    """Translate an arXiv category code into a human-readable venue name."""
    extra = paper.get("extra") if isinstance(paper.get("extra"), dict) else {}
    raw_values = [
        paper.get("primary_category"),
        paper.get("arxiv_primary_category"),
        paper.get("categories"),
        extra.get("primary_category"),
        extra.get("arxiv_primary_category"),
        extra.get("categories"),
    ]
    for raw in raw_values:
        values = (
            raw
            if isinstance(raw, (list, tuple, set))
            else re.split(r"[;,]\s*", _paper_value(raw))
        )
        for value in values:
            text = _paper_value(value).strip()
            if not text:
                continue
            if ":" in text and text.lower().startswith("arxiv"):
                text = text.split(":", 1)[1].strip()
            mapped = ARXIV_CATEGORY_VENUES.get(text)
            if mapped:
                return mapped
    return ""


# =========================================================================
# 7.  Title similarity
# =========================================================================


def _title_similarity(left: str, right: str) -> float:
    """Jaccard similarity of alphanumeric tokens between two strings."""
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _repository_paper_matches_request(
    paper: Any, *, doi: str, title: str
) -> bool:
    """Check whether a Paper object matches a requested DOI/title."""
    expected_doi = (doi or "").strip().lower()
    paper_doi = _paper_value(getattr(paper, "doi", "")).strip().lower()
    if expected_doi and paper_doi and paper_doi == expected_doi:
        return True

    expected_arxiv = _extract_arxiv_id(doi, title)
    paper_arxiv = _extract_arxiv_id(
        getattr(paper, "doi", ""),
        getattr(paper, "paper_id", ""),
        getattr(paper, "url", ""),
        getattr(paper, "pdf_url", ""),
    )
    if expected_arxiv and paper_arxiv and expected_arxiv == paper_arxiv:
        return True

    if title:
        paper_title = _paper_value(getattr(paper, "title", ""))
        if _title_similarity(title, paper_title) >= 0.72:
            return True

    return not (expected_doi or title)


# =========================================================================
# 8.  Publication metadata helpers
# =========================================================================


def _paper_publication_date(paper: Dict[str, Any]) -> str:
    """Return the best publication-date string from common fields."""
    for field in (
        "published_date",
        "publication_date",
        "published_at",
        "date",
        "year",
        "updated_date",
    ):
        value = _paper_field(paper, field)
        if value:
            return value
    return _paper_extra_value(
        paper, "publication_date", "published_date", "date", "year"
    )


def _paper_publication_venue(paper: Dict[str, Any]) -> str:
    """Return the most specific publication venue, falling back to arXiv category."""
    for field in (
        "journal",
        "journal_ref",
        "venue",
        "publication_venue",
        "container_title",
        "container-title",
        "publisher",
    ):
        value = _paper_field(paper, field)
        if value and not _is_generic_publication_venue(value):
            return value
    extra_value = _paper_extra_value(
        paper,
        "journal",
        "journal_title",
        "journal_ref",
        "venue",
        "publication_venue",
        "container_title",
        "publisher",
        "publication_info",
    )
    if extra_value and not _is_generic_publication_venue(extra_value):
        return extra_value
    return _arxiv_category_venue(paper)


def _paper_original_url(
    paper: Dict[str, Any], *, url: str = "", doi: str = "", pdf_url: str = ""
) -> str:
    """Return a paper's HTML landing-page URL, constructing one from DOI if needed."""
    for value in (
        url,
        _paper_field(paper, "original_url"),
        _paper_field(paper, "landing_page_url"),
    ):
        if value:
            return value
    extra_url = _paper_extra_value(paper, "landing_page_url", "url", "openalex_id")
    if extra_url:
        return extra_url
    if doi:
        return f"https://doi.org/{doi}"
    return pdf_url


# =========================================================================
# 9.  Paper scoring
# =========================================================================


def _paper_score(paper: Dict[str, Any], query: str = "") -> float:
    """Compute a relevance / quality score for a paper record."""
    score = 0.0
    title = _normalize_lookup_text(paper.get("title"))
    query_terms = [
        term for term in re.findall(r"[\w]+", query.lower()) if len(term) > 2
    ]
    if query_terms and title:
        matched = sum(1 for term in query_terms if term in title)
        score += 3.0 * matched / max(1, len(query_terms))
    if _paper_value(paper.get("doi")).strip():
        score += 1.2
    if _paper_has_pdf_signal(paper):
        score += 2.0
    # Arxiv source boost -- arxiv PDF URLs are more stable and reliable
    if _env_bool(PREFER_ARXIV_ENV, True):
        sources = _paper_sources(paper)
        if "arxiv" in sources:
            score += 0.5
    # Download-capability boost -- prefer papers from sources that can
    # actually serve PDFs.  Metadata-only sources (crossref, openalex, dblp,
    # etc.) get NO boost so they fall below downloadable papers in ranking.
    sources = _paper_sources(paper)
    if sources:
        best_download = _best_source_download_capability(sources)
        if best_download is True:
            score += 3.0  # strong boost: arxiv, biorxiv, medrxiv, iacr
        elif best_download and best_download != "record_dependent":
            score += 1.0  # moderate: oa_pdf sources like semantic, pmc, europepmc
    score += min(1.0, _paper_source_reliability_score(paper) / 100.0)
    source_count = len(sources)
    score += min(source_count, 5) * 0.25
    year = _paper_year_number(paper)
    if year:
        score += max(0.0, min(1.0, (year - 2018) / 8.0))
    citations = _paper_citations(paper)
    if citations:
        score += min(1.5, citations / 200.0)
    return round(score, 4)


# =========================================================================
# 10.  Agent-skill ranking profile
# =========================================================================

AGENT_SKILL_RANKING_PROFILE = "agent-skill"
AGENT_SKILL_PROFILE_ALIASES = {
    AGENT_SKILL_RANKING_PROFILE,
    "agent_skill",
    "agentskill",
    "skill-agent",
}
AGENT_SKILL_BOOST_PHRASES = [
    "agent skill",
    "agent skills",
    "agentic skill",
    "skill library",
    "skill libraries",
    "skill retrieval",
    "skill ecosystem",
    "skill security",
    "skill audit",
    "skill revision",
    "skillbench",
    "skillsbench",
    "skill.md",
    "llm agent",
    "llm agents",
    "language agent",
    "software agent",
    "tool-using agent",
    "tool using agent",
]
AGENT_SKILL_AGENT_TERMS = {
    "agent",
    "agents",
    "agentic",
    "llm",
    "language model",
    "tool",
}
AGENT_SKILL_SKILL_TERMS = {
    "skill",
    "skills",
    "capability",
    "capabilities",
    "library",
    "retrieval",
}
AGENT_SKILL_NEGATIVE_PHRASES = [
    "human skill",
    "human skills",
    "motor skill",
    "social skill",
    "piano skill",
    "clinical skill",
    "surgical skill",
    "teaching skill",
    "workforce skill",
]


def _paper_profile_text(paper: Dict[str, Any]) -> str:
    """Concatenate textual fields used for profile scoring."""
    fields = [
        paper.get("title"),
        paper.get("abstract"),
        paper.get("summary"),
        paper.get("keywords"),
        paper.get("categories"),
        paper.get("venue"),
    ]
    return " ".join(_paper_value(field) for field in fields if field)


def _agent_skill_profile_score(paper: Dict[str, Any]) -> float:
    """Score a paper's relevance to the agent-skill ranking profile."""
    text = _normalize_lookup_text(_paper_profile_text(paper))
    title = _normalize_lookup_text(paper.get("title"))
    if not text:
        return 0.0

    score = 0.0
    for phrase in AGENT_SKILL_BOOST_PHRASES:
        if phrase in text:
            score += 3.0
            if phrase in title:
                score += 1.5

    has_agent = any(term in text for term in AGENT_SKILL_AGENT_TERMS)
    has_skill = any(term in text for term in AGENT_SKILL_SKILL_TERMS)
    if has_agent and has_skill:
        score += 4.0
    elif has_skill:
        score += 0.5

    for phrase in AGENT_SKILL_NEGATIVE_PHRASES:
        if phrase in text:
            score -= 4.0
            if phrase in title:
                score -= 2.0

    # 无法获取 PDF 的论文扣分，排在末尾但不过度惩罚
    # 保留在候选池中，以便 _recommended_display_candidates 的 Phase-3
    # fallback 能够在 downloadable 论文不足时填充到 requested_count。
    if paper.get("download_ready") is False:
        score -= 5.0

    if "multi-agent" in text or "multi agent" in text:
        score += 1.0
    if "benchmark" in text and has_agent and has_skill:
        score += 1.0
    return round(score, 4)


def _ranking_profile_name(ranking_profile: str = "") -> str:
    """Resolve a ranking profile string to its canonical name."""
    profile = (ranking_profile or "").strip().lower()
    return (
        AGENT_SKILL_RANKING_PROFILE
        if profile in AGENT_SKILL_PROFILE_ALIASES
        else profile
    )


def _rank_papers_for_profile(
    papers: List[Dict[str, Any]],
    *,
    ranking_profile: str = "",
    query: str = "",
) -> List[Dict[str, Any]]:
    """Re-score and sort papers using a named ranking profile."""
    profile = _ranking_profile_name(ranking_profile)
    if not profile:
        return list(papers)
    if profile != AGENT_SKILL_RANKING_PROFILE:
        return list(papers)

    ranked: List[Dict[str, Any]] = []
    for paper in papers:
        item = dict(paper)
        base_score = float(item.get("score") or _paper_score(item, query=query))
        profile_score = _agent_skill_profile_score(item)
        item["ranking_profile"] = AGENT_SKILL_RANKING_PROFILE
        item["profile_score"] = round(profile_score, 4)
        item["score"] = round(base_score + profile_score, 4)
        ranked.append(item)
    ranked.sort(
        key=lambda paper: (
            float(paper.get("score") or 0),
            float(paper.get("profile_score") or 0),
        ),
        reverse=True,
    )
    return ranked


# =========================================================================
# 11.  Merging and deduplication
# =========================================================================


def _merge_paper_record(
    base: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge *incoming* paper metadata into *base*, tracking sources."""
    merged = dict(base)
    for key, value in incoming.items():
        if key in {"source_records", "sources", "score", "score_reasons"}:
            continue
        if value not in (None, "", [], {}) and merged.get(key) in (
            None,
            "",
            [],
            {},
        ):
            merged[key] = value

    sources = list(merged.get("sources") or [])
    for source in (
        _paper_value(base.get("source")).strip(),
        _paper_value(incoming.get("source")).strip(),
    ):
        if source and source not in sources:
            sources.append(source)
    merged["sources"] = sources

    # Prefer arxiv canonical attributes when merging (arxiv PDF URLs are
    # more stable)
    if _env_bool(PREFER_ARXIV_ENV, True):
        incoming_source = _paper_value(incoming.get("source")).strip().lower()
        base_source = _paper_value(base.get("source")).strip().lower()
        if incoming_source == "arxiv" and base_source != "arxiv":
            for field in ("paper_id", "pdf_url", "url"):
                arxiv_val = _paper_value(incoming.get(field)).strip()
                if arxiv_val:
                    merged[field] = arxiv_val

    records = list(merged.get("source_records") or [])
    records.append(
        {
            "source": _paper_value(incoming.get("source")).strip(),
            "paper_id": _paper_value(incoming.get("paper_id")).strip(),
            "doi": _paper_value(incoming.get("doi")).strip(),
            "pdf_url": _paper_value(incoming.get("pdf_url")).strip(),
            "url": _paper_value(incoming.get("url")).strip(),
        }
    )
    merged["source_records"] = records
    return merged


# ---------------------------------------------------------------------------
# Title-similarity threshold for second-pass dedup (Section 11)
# ---------------------------------------------------------------------------
_TITLE_SIMILARITY_DEDUP_THRESHOLD = 0.85


def _dedupe_papers(
    papers: List[Dict[str, Any]], query: str = ""
) -> List[Dict[str, Any]]:
    """Deduplicate a list of paper dicts, merging duplicates and re-scoring.

    Two-pass strategy:
    1. **Key-based merge** — DOI > arXiv ID > title+authors > paper_id.
       Catches explicit identifiers and exact title/author matches.
    2. **Title-similarity merge** — Jaccard similarity on title tokens ≥ 0.85.
       Catches the same paper from different sources when identifiers are
       missing and author formatting differs (e.g. "J. Smith" vs "John Smith").
       Papers must also share a publication year when both have one.
    """
    # ── Pass 1: key-based merge ──────────────────────────────────────
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        key = _paper_unique_key(paper)
        if key not in by_key:
            merged = dict(paper)
            source = _paper_value(merged.get("source")).strip()
            merged["sources"] = [source] if source else []
            merged["source_records"] = [
                {
                    "source": source,
                    "paper_id": _paper_value(merged.get("paper_id")).strip(),
                    "doi": _paper_value(merged.get("doi")).strip(),
                    "pdf_url": _paper_value(merged.get("pdf_url")).strip(),
                    "url": _paper_value(merged.get("url")).strip(),
                }
            ]
            by_key[key] = merged
            order.append(key)
        else:
            by_key[key] = _merge_paper_record(by_key[key], paper)

    deduped = [by_key[key] for key in order]

    # ── Pass 2: title-similarity merge ───────────────────────────────
    # Merge papers whose titles are highly similar but weren't caught
    # by the primary key (e.g. missing DOI, author-formatting drift).
    if len(deduped) > 1:
        deduped = _title_similarity_dedup_pass(deduped)

    # ── Score & sort ─────────────────────────────────────────────────
    for paper in deduped:
        paper["score"] = _paper_score(paper, query=query)
    _prefer_arxiv = _env_bool(PREFER_ARXIV_ENV, True)
    deduped.sort(
        key=lambda paper: (
            float(paper.get("score") or 0),
            1
            if _best_source_download_capability(
                _paper_sources(paper)
            )
            else 0,
            _paper_source_reliability_score(paper),
            1
            if _prefer_arxiv
            and "arxiv"
            in _paper_sources(paper)
            else 0,
        ),
        reverse=True,
    )
    # After sorting with download preference, if we still have fewer
    # downloadable papers than requested and need to fill with metadata-only
    # papers, the sort already places them correctly (downloadable first).
    return deduped


def _title_similarity_dedup_pass(
    papers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Second-pass dedup: merge papers whose titles are nearly identical.

    Only merges when:
    - Jaccard title-token similarity ≥ threshold (default 0.85)
    - Publication years match when both papers have a year
    - Neither paper already has a DOI-based key (DOI = authoritative match)

    The paper that appears first in the list absorbs matching later papers.
    """
    remaining = list(papers)
    result: List[Dict[str, Any]] = []

    while remaining:
        anchor = remaining.pop(0)
        anchor_title = _normalize_lookup_text(anchor.get("title"))
        anchor_year = _paper_year_number(anchor)
        # Skip title-similarity for DOI-anchored papers — DOI matching
        # in pass 1 is authoritative and these don't need fuzzy merge.
        anchor_has_doi = bool(_paper_value(anchor.get("doi")).strip())

        merged_indices: List[int] = []
        for idx, candidate in enumerate(remaining):
            if anchor_has_doi and _paper_value(candidate.get("doi")).strip():
                # Both have DOIs but keys didn't match — they are genuinely
                # different papers.  Skip title-similarity to avoid false merges.
                continue

            candidate_title = _normalize_lookup_text(candidate.get("title"))
            similarity = _title_similarity(anchor_title, candidate_title)
            if similarity < _TITLE_SIMILARITY_DEDUP_THRESHOLD:
                continue

            # Year guard: only merge when years are consistent
            candidate_year = _paper_year_number(candidate)
            if anchor_year and candidate_year and anchor_year != candidate_year:
                continue

            # Merge candidate into anchor
            anchor = _merge_paper_record(anchor, candidate)
            merged_indices.append(idx)

        result.append(anchor)
        # Remove merged papers (iterate in reverse to preserve indices)
        for idx in reversed(merged_indices):
            remaining.pop(idx)

    return result


# =========================================================================
# 12.  Parse candidate construction
# =========================================================================


def _paper_parse_candidate(
    paper: Dict[str, Any], index: int
) -> Dict[str, Any]:
    """Build a standardised parse-candidate record from a paper dict."""
    source = _paper_field(paper, "source").lower()
    paper_id = _paper_field(paper, "paper_id")
    doi = _paper_doi(paper)
    title = _paper_field(paper, "title")
    pdf_url = _paper_field(paper, "pdf_url")
    local_pdf_path = _paper_field(paper, "local_pdf_path") or _paper_field(
        paper, "pdf_path"
    )
    url = _paper_field(paper, "url")
    arxiv_id = _extract_arxiv_id(paper_id, doi, pdf_url, url)
    if arxiv_id:
        source = "arxiv"
        paper_id = arxiv_id
        if not pdf_url or "arxiv.org/abs/" in pdf_url.lower():
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    download_ready, reason, download_confidence = _download_route_for_candidate(
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        pdf_url=pdf_url,
        local_pdf_path=local_pdf_path,
        arxiv_id=arxiv_id,
    )
    parse_ready = download_ready

    candidate = {
        "index": index,
        "title": title,
        "authors": _paper_field(paper, "authors"),
        "year": _paper_year(paper),
        "published_date": _paper_publication_date(paper),
        "publication_venue": _paper_publication_venue(paper),
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "pdf_url": pdf_url,
        "local_pdf_path": local_pdf_path,
        "url": url,
        "original_url": _paper_original_url(
            paper, url=url, doi=doi, pdf_url=pdf_url
        ),
        "canonical_pdf_stem": _canonical_pdf_stem(
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            pdf_url=pdf_url,
            url=url,
            fallback=f"paper_{index}",
        ),
        "parse_ready": parse_ready,
        "download_ready": download_ready,
        "download_route": reason,
        "download_confidence": download_confidence,
        "reason": reason,
    }
    for key in ("source_index", "original_index"):
        if key in paper:
            try:
                candidate[key] = int(paper.get(key) or 0)
            except (TypeError, ValueError):
                candidate[key] = paper.get(key)
    return candidate


# =========================================================================
# 13.  Searcher resolver (searcher instances live in server.py)
# =========================================================================


def _searcher_for_source(
    source: str, searchers: Optional[Dict[str, Any]] = None
) -> Any:
    """Look up a searcher instance by source name from a caller-supplied dict.

    Args:
        source: Lower-case source name (e.g. ``'arxiv'``).
        searchers: Dict mapping source name to searcher instance.
                   If ``None``, returns ``None``.

    Returns:
        The searcher instance or ``None``.
    """
    if searchers is None:
        searchers = {}
    return searchers.get((source or "").strip().lower())
