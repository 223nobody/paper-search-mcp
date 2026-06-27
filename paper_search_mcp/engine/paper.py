# paper_search_mcp/engine/paper.py
"""
Paper value normalisation, scoring, deduplication, ranking, and parse-candidate
construction.

Extracted from server.py.  This module has **zero** MCP / FastMCP dependencies.

**Ranking profiles** are data-driven.  Each profile is a ``ProfileSpec`` instance
registered via ``register_profile()``.  The built-in ``agent-skill`` profile is
defined this way, and new domain profiles (``cv``, ``nlp``, ``security``, etc.)
can be added by defining additional ``ProfileSpec`` values without changing core
scoring logic.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple

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
# 0.  Scoring weight configuration (tuneable via environment)
# =========================================================================

# All scoring weights are read once at import time.  Override them by setting
# the corresponding PAPER_SEARCH_MCP_ environment variable.
def _scoring_weight(name: str, default: float) -> float:
    raw = get_env(name, str(default)).strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


SCORING_WEIGHTS = {
    # ── Query relevance ──────────────────────────────────────────────
    "title_term_match": _scoring_weight("SCORE_W_TITLE_MATCH", 3.0),
    "abstract_term_match": _scoring_weight("SCORE_W_ABSTRACT_MATCH", 1.5),
    "keyword_term_match": _scoring_weight("SCORE_W_KEYWORD_MATCH", 0.8),
    "category_match": _scoring_weight("SCORE_W_CATEGORY_MATCH", 1.0),
    "rare_term_boost": _scoring_weight("SCORE_W_RARE_TERM", 0.5),
    # ── Identity / provenance ────────────────────────────────────────
    "doi_present": _scoring_weight("SCORE_W_DOI", 1.2),
    "pdf_signal": _scoring_weight("SCORE_W_PDF_SIGNAL", 2.0),
    "arxiv_source": _scoring_weight("SCORE_W_ARXIV_SOURCE", 0.5),
    # ── Download capability ──────────────────────────────────────────
    "direct_download": _scoring_weight("SCORE_W_DIRECT_DOWNLOAD", 3.0),
    "oa_download": _scoring_weight("SCORE_W_OA_DOWNLOAD", 1.0),
    # ── Source signals ───────────────────────────────────────────────
    "source_reliability": _scoring_weight("SCORE_W_SOURCE_RELIABILITY", 1.0),
    "source_count": _scoring_weight("SCORE_W_SOURCE_COUNT", 0.25),
    "max_source_count": _scoring_weight("SCORE_MAX_SOURCE_COUNT", 5),
    # ── Temporal ─────────────────────────────────────────────────────
    "year_decay_rate": _scoring_weight("SCORE_YEAR_DECAY", 3.0),
    "year_max": _scoring_weight("SCORE_YEAR_MAX", 1.0),
    # ── Impact ───────────────────────────────────────────────────────
    "citation_divisor": _scoring_weight("SCORE_CITATION_DIVISOR", 200.0),
    "citation_max": _scoring_weight("SCORE_CITATION_MAX", 1.5),
    # ── Venue prestige ───────────────────────────────────────────────
    "top_venue": _scoring_weight("SCORE_W_TOP_VENUE", 1.5),
    "major_venue": _scoring_weight("SCORE_W_MAJOR_VENUE", 0.5),
    # ── Profile scoring ──────────────────────────────────────────────
    "profile_boost_phrase": _scoring_weight("SCORE_W_PROFILE_BOOST", 3.0),
    "profile_boost_title": _scoring_weight("SCORE_W_PROFILE_BOOST_TITLE", 1.5),
    "profile_term_pair": _scoring_weight("SCORE_W_PROFILE_TERM_PAIR", 4.0),
    "profile_term_single": _scoring_weight("SCORE_W_PROFILE_TERM_SINGLE", 0.5),
    "profile_negative": _scoring_weight("SCORE_W_PROFILE_NEGATIVE", 4.0),
    "profile_negative_title": _scoring_weight("SCORE_W_PROFILE_NEGATIVE_TITLE", 2.0),
    "profile_non_downloadable": _scoring_weight("SCORE_W_NON_DOWNLOADABLE", 5.0),
    "profile_multi_agent": _scoring_weight("SCORE_W_MULTI_AGENT", 1.0),
    "profile_benchmark": _scoring_weight("SCORE_W_BENCHMARK", 1.0),
    # ── Phase 3: text processing enhancements ─────────────────────────
    "bigram_match": _scoring_weight("SCORE_W_BIGRAM_MATCH", 1.5),
    "stem_match_boost": _scoring_weight("SCORE_W_STEM_MATCH", 0.3),
}

# =========================================================================
# 0d.  Lightweight academic-English stemmer & IDF dictionary
# =========================================================================

# Minimal suffix-stripping stemmer focused on academic CS vocabulary.
# Adds ~0.01 ms per token — negligible compared to network latency (seconds).
#
# This is NOT a full Porter stemmer.  It handles the most frequent English
# suffixes in CS papers.  For words not covered, the original form is kept.
_STEM_SUFFIX_RULES: List[Tuple[str, str, int]] = [
    # (suffix, replacement, min_stem_length)
    ("izational", "ize", 5),
    ("ationally", "ate", 5),
    ("tional", "tion", 5),
    ("izations", "ize", 5),
    ("isation", "ise", 5),
    ("istical", "istic", 5),
    ("encing", "ence", 4),
    ("ancing", "ance", 4),
    ("izable", "ize", 5),
    ("istical", "ist", 5),
    ("fulness", "ful", 4),
    ("ousness", "ous", 4),
    ("alities", "al", 5),
    ("iveness", "ive", 4),
    ("ements", "ement", 4),
    ("ically", "ic", 4),
    ("ations", "ate", 4),
    ("atives", "ative", 4),
    ("nesses", "ness", 4),
    ("tional", "tion", 4),
    ("enting", "ent", 4),
    ("ations", "ate", 4),
    ("alists", "al", 4),
    ("abling", "able", 4),
    ("ically", "ic", 4),
    ("istics", "ist", 4),
    ("ifying", "ify", 4),
    ("alized", "al", 4),
    ("alizes", "al", 4),
    ("arized", "ar", 4),
    ("atedly", "ate", 4),
    ("bilities", "ble", 4),
    ("eloping", "elop", 4),
    ("eliable", "ely", 4),
    ("entally", "ent", 4),
    ("ential", "ent", 4),
    ("erating", "erate", 4),
    ("erences", "erence", 4),
    ("erizing", "erize", 4),
    ("esizing", "esize", 4),
    ("gencies", "gent", 4),
    ("gencies", "gent", 4),
    ("j ects", "ject", 4),
    # Shorter suffixes (most frequent, applied last)
    ("ingly", "ing", 5),
    ("ingly", "", 5),
    ("ement", "", 5),
    ("ments", "", 5),
    ("ation", "ate", 5),
    ("ators", "ate", 5),
    ("iveness", "ive", 5),
    ("ities", "ity", 4),
    ("ively", "ive", 4),
    ("izing", "ize", 4),
    ("ising", "ise", 4),
    ("istic", "ist", 4),
    ("ables", "able", 4),
    ("ional", "ion", 4),
    ("ions", "ion", 4),
    ("ment", "", 4),
    ("ness", "", 4),
    ("ship", "", 4),
    ("ings", "ing", 4),
    ("ting", "te", 4),
    ("ding", "de", 4),
    ("lling", "ll", 4),
    ("ssing", "ss", 4),
    ("king", "k", 3),
    ("ping", "p", 3),
    ("cing", "ce", 3),
    ("ging", "ge", 3),
    ("ming", "m", 3),
    ("ning", "n", 3),
    ("ring", "r", 3),
    ("sing", "se", 3),
    ("ting", "t", 3),
    ("ving", "ve", 3),
    ("zing", "ze", 3),
    ("ied", "y", 3),
    ("ies", "y", 3),
    ("ier", "y", 3),
    ("est", "", 3),
    ("ing", "", 4),
    ("eed", "ee", 3),
    ("eed", "ee", 3),
    ("ers", "er", 3),
    ("ors", "or", 3),
    ("ists", "ist", 3),
    ("ans", "an", 3),
    ("ous", "", 4),
    ("ful", "", 4),
    ("ive", "", 4),
    ("able", "", 4),
    ("ible", "", 4),
    ("ial", "", 4),
    ("ical", "ic", 4),
    ("ally", "al", 4),
    ("edly", "ed", 4),
    ("s", "", 4),
    ("es", "", 5),
    ("ed", "", 3),
    ("ly", "", 3),
    ("er", "", 3),
    ("or", "", 3),
    ("ar", "", 3),
]
# Deduplicate while preserving order
_SEEN: set = set()
_STEM_RULES_UNIQ: List[Tuple[str, str, int]] = []
for _sfx, _rep, _ml in _STEM_SUFFIX_RULES:
    _key = (_sfx, _rep, _ml)
    if _key not in _SEEN:
        _SEEN.add(_key)
        _STEM_RULES_UNIQ.append(_key)
_STEM_SUFFIX_RULES = _STEM_RULES_UNIQ
del _SEEN, _STEM_RULES_UNIQ, _sfx, _rep, _ml, _key

# Static IDF-like rarity weights for common CS terms.
# Higher = more discriminative.  Computed from arXiv category-level term
# frequencies; loaded once at import time.
_TERM_RARITY: Dict[str, float] = {
    # Rare / highly discriminative
    "segmentation": 2.5, "transformer": 2.0, "adversarial": 2.2,
    "diffusion": 2.3, "nerf": 3.0, "gaussian": 2.0, "splatting": 3.5,
    "privacy": 2.0, "encryption": 2.5, "cryptographic": 3.0,
    "federated": 2.8, "homomorphic": 3.5, "blockchain": 2.5,
    "slam": 3.0, "grasping": 2.8, "kinematic": 2.5,
    "compiler": 2.0, "verification": 1.8, "synthesis": 1.8,
    "concurrency": 2.5, "consensus": 2.0, "byzantine": 3.0,
    "quantum": 3.0, "qubit": 4.0, "entanglement": 3.5,
    # Medium discriminative
    "detection": 1.3, "recognition": 1.3, "generation": 1.2,
    "classification": 1.1, "retrieval": 1.5, "ranking": 1.5,
    "embedding": 1.5, "attention": 1.3, "graph": 1.2,
    "reinforcement": 2.0, "bayesian": 2.0, "variational": 2.5,
    "convolutional": 1.8, "recurrent": 2.0, "residual": 1.8,
    "distributed": 1.3, "scalable": 1.5, "fault-tolerant": 2.5,
    "efficient": 1.0, "robust": 1.2, "adaptive": 1.3,
    "interpretable": 2.5, "explainable": 3.0, "fairness": 2.5,
    "multimodal": 2.0, "cross-modal": 3.0, "zero-shot": 3.0,
}
_DEFAULT_RARITY = 1.0


def _stem_word(word: str) -> str:
    """Apply lightweight suffix-stripping to *word* and return the stem.

    Only handles the most common English suffixes found in CS papers.
    Falls back to the original word when no rule matches.

    Perf: ~1 μs per word (pure string ops, no regex).
    """
    if len(word) <= 3:
        return word
    word_lower = word.lower()
    for suffix, replacement, min_stem in _STEM_SUFFIX_RULES:
        if word_lower.endswith(suffix) and len(word_lower) - len(suffix) >= min_stem:
            return word_lower[: len(word_lower) - len(suffix)] + replacement
    return word_lower


def _stemmed_tokens(text: str) -> Set[str]:
    """Return the set of original + stemmed tokens from *text*.

    Used to expand the match surface: "learning" matches both "learning"
    and "learn".
    """
    tokens = _token_set(text)
    result = set(tokens)
    for token in tokens:
        stem = _stem_word(token)
        if stem and stem != token:
            result.add(stem)
    return result


def _query_bigrams(terms: List[str]) -> List[str]:
    """Extract adjacent bigrams from query terms for phrase-aware matching."""
    if len(terms) < 2:
        return []
    return [f"{terms[i]} {terms[i + 1]}" for i in range(len(terms) - 1)]


def _classify_query_intent(
    query: str, *, top_k: int = 3
) -> List[Tuple[str, float]]:
    """Auto-detect which ranking profiles best match a natural-language query.

    Returns up to *top_k* (profile_name, confidence) pairs sorted by confidence.
    Confidence is based on overlap between query terms and each profile's
    boost_phrases, term_groups, and category_boost.

    Perf: O(profiles × phrases) ≈ 11 × 20 = 220 string comparisons.
    Runs once per query, negligible vs 18s+ search time.
    """
    if not query:
        return []
    query_lower = query.lower()
    query_terms = _query_terms(query)

    scores: List[Tuple[str, float]] = []
    seen_specs: set = set()
    for spec in _RANKING_PROFILES.values():
        if spec.name in seen_specs:
            continue
        seen_specs.add(spec.name)
        confidence = 0.0
        # Boost phrase overlap
        for phrase in spec.boost_phrases:
            if phrase in query_lower:
                confidence += 2.0
                if query_lower.startswith(phrase):
                    confidence += 1.0
        # Term group overlap
        for terms in spec.term_groups.values():
            matched = sum(
                1 for t in terms
                if _term_matches(query_lower, t)
            )
            if terms:
                confidence += matched / len(terms) * 1.5
        # Category boost overlap
        if spec.category_boost:
            for cat in spec.category_boost:
                cat_lower = cat.lower()
                if cat_lower in query_lower or cat_lower.replace("cs.", "") in query_lower:
                    confidence += 1.5
                    break
        if confidence > 0:
            scores.append((spec.name, round(confidence, 3)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]

# =========================================================================
# 0b.  Venue prestige database
# =========================================================================

# Tier-1: top-tier conferences and journals across CS domains
_TOP_VENUES: Set[str] = {
    # AI / ML / CV / NLP
    "neurips", "nips", "icml", "iclr", "aaai", "ijcai", "uai", "aistats",
    "cvpr", "iccv", "eccv", "acl", "emnlp", "naacl", "coling", "tacl",
    # Systems / Networks / Architecture
    "osdi", "sosp", "eurosys", "usenix atc", "usenix", "nsdi", "sigcomm",
    "mobicom", "mobisys", "sensys", "isca", "micro", "hpca", "asplos",
    "fast", "sc", "ppopp", "pldi", "popl", "oopsla", "splash",
    # Security / Crypto
    "ccs", "ieee s&p", "ieee s p", "usenix security", "ndss",
    "crypto", "eurocrypt", "asiacrypt", "tcc", "pkc", "ches",
    # Software Engineering
    "icse", "fse", "esec/fse", "ase", "issta", "oopsla",
    # Databases
    "sigmod", "vldb", "icde", "pods",
    # HCI / Graphics
    "chi", "uist", "ubicomp", "cscw", "siggraph", "siggraph asia",
    # Theory
    "stoc", "focs", "soda", "icalp",
    # Robotics
    "icra", "iros", "rss",
    # Interdisciplinary top journals
    "nature", "science", "cell", "pnas", "nature communications",
    "science advances", "nature methods", "nature machine intelligence",
    # Top CS journals
    "jacm", "tacm", "ieee tpami", "ijcv", "ieee tifs", "ieee tdsc",
    "acm computing surveys", "ieee tse", "acm toplas",
    "ieee tit", "journal of machine learning research", "jmlr",
}

# Tier-2: well-respected venues — still worth a moderate boost
_MAJOR_VENUES: Set[str] = {
    # AI / ML
    "ecai", "ecml", "pkdd", "icann", "ijcnn", "wacv", "bmvc", "accv",
    "icassp", "interspeech", "conll", "eacl", "inlg", "*sem",
    # Systems
    "vee", "icdcs", "middleware", "sosp", "sigmetrics", "imc",
    "ipsn", "rtss", "rtas", "date", "dac", "iccad", "cases",
    # Security
    "acsac", "raid", "esorics", "dac", "dimva", "securecomm",
    "fc", "crypto", "pets",
    # SE / PL
    "msr", "icsme", "saner", "icpc", "ecoop", "cc", "cgo",
    # DB
    "edbt", "cikm", "www", "wsdm", "kdd",
    # HCI
    "mobilehci", "dis", "interact", "nordichi",
    # Theory
    "soda", "socg", "ccc", "icalp", "esa", "stacs",
    # Robotics
    "humanoids", "case", "icar",
}

_VENUE_NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")


def _normalize_venue_name(name: str) -> str:
    """Normalize a venue name for prestige lookup."""
    return _VENUE_NORMALIZE_RE.sub(" ", name.strip().lower())


def _venue_prestige_score(paper: Dict[str, Any]) -> float:
    """Score a paper based on its publication venue prestige."""
    venue_fields = [
        paper.get("venue"),
        paper.get("journal"),
        paper.get("journal_ref"),
        paper.get("container_title"),
        paper.get("publication_venue"),
        paper.get("publisher"),
    ]
    extra = paper.get("extra")
    if isinstance(extra, dict):
        venue_fields.extend([
            extra.get("venue"),
            extra.get("journal_title"),
            extra.get("container_title"),
            extra.get("publication_info"),
        ])

    for raw in venue_fields:
        if not raw:
            continue
        norm = _normalize_venue_name(_paper_value(raw))
        if norm in _TOP_VENUES:
            return SCORING_WEIGHTS["top_venue"]
        if norm in _MAJOR_VENUES:
            return SCORING_WEIGHTS["major_venue"]

    # Also check arXiv category venue (human-readable)
    arxiv_venue = _arxiv_category_venue(paper)
    if arxiv_venue:
        norm = _normalize_venue_name(arxiv_venue)
        if norm in _TOP_VENUES:
            return SCORING_WEIGHTS["top_venue"]
        if norm in _MAJOR_VENUES:
            return SCORING_WEIGHTS["major_venue"]

    return 0.0


# =========================================================================
# 0c.  Pluggable ranking profile framework
# =========================================================================


@dataclass
class ProfileSpec:
    """Data-driven definition of a paper-ranking profile.

    Each profile defines:
    - **boost_phrases**: high-weight exact phrases (e.g. "agent skill")
    - **term_groups**: named sets of terms that are matched with word boundaries.
      When a paper contains terms from *all* non-empty groups, a bonus is awarded.
    - **negative_phrases**: phrases that downgrade the paper
    - **category_boost**: arXiv categories associated with this domain (matched
      against ``primary_category`` or ``categories`` in the paper record).
    - **description**: human-readable label shown in reports.
    """

    name: str
    aliases: Set[str] = field(default_factory=set)
    description: str = ""
    boost_phrases: List[str] = field(default_factory=list)
    term_groups: Dict[str, Set[str]] = field(default_factory=dict)
    negative_phrases: List[str] = field(default_factory=list)
    category_boost: Set[str] = field(default_factory=set)
    # Per-group weights override the default term_pair / term_single values
    term_group_weights: Dict[str, float] = field(default_factory=dict)

    def all_names(self) -> Set[str]:
        return {self.name} | self.aliases


# Profile registry
_RANKING_PROFILES: Dict[str, ProfileSpec] = {}


def register_profile(spec: ProfileSpec) -> None:
    """Register a ranking profile so it can be selected by name or alias."""
    for name in spec.all_names():
        _RANKING_PROFILES[name] = spec


def get_profile(name: str) -> Optional[ProfileSpec]:
    """Look up a registered profile by name or alias (None if not found)."""
    return _RANKING_PROFILES.get((name or "").strip().lower())


def list_profiles() -> Dict[str, str]:
    """Return {canonical_name: description} for all registered profiles."""
    seen: Set[str] = set()
    result: Dict[str, str] = {}
    for spec in _RANKING_PROFILES.values():
        if spec.name not in seen:
            seen.add(spec.name)
            result[spec.name] = spec.description
    return result


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
    """Collapse whitespace, lowercase, and NFKC-normalize for fuzzy-lookup keys.

    Unicode NFKC decomposes ligatures (ﬁ→fi), fullwidth letters (Ａ→A),
    and other compatibility forms.  Runs at C speed (~1 μs per call).
    """
    raw = _paper_value(value).strip()
    normalized = unicodedata.normalize("NFKC", raw)
    return re.sub(r"\s+", " ", normalized.lower())


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


def _query_terms(query: str) -> List[str]:
    """Extract meaningful query terms, filtering short/stop words."""
    # Minimal stop words — common in academic queries but low signal
    _stop_words = {
        "the", "and", "for", "with", "from", "that", "this", "are", "was",
        "have", "has", "been", "were", "its", "not", "but", "all", "can",
        "using", "based", "via", "into", "such", "than", "also", "more",
    }
    terms = re.findall(r"[a-z0-9]+", query.lower())
    return [
        t for t in terms
        if len(t) > 2 and t not in _stop_words
    ]


def _query_term_match_ratio(terms: List[str], text: str) -> float:
    """Fraction of query terms found in *text*, with bigram and rarity bonuses.

    Returns a score in [0.0, 1.0 + bonuses] where:
    - Base: fraction of unigram terms matched in text
    - Bigram bonus: +bigram_weight for each complete bigram matched
    - Rarity bonus: rare terms contribute more per match
    - Stem bonus: stems of query terms also checked against stemmed text tokens

    Perf: O(|terms| + |bigrams|) string-in-string checks, ~50 μs for typical query.
    """
    if not terms or not text:
        return 0.0
    W = SCORING_WEIGHTS

    # ── Unigram base score ─────────────────────────────────────────
    matched = 0
    rarity_sum = 0.0
    for t in terms:
        if t in text:
            matched += 1
            rarity_sum += _TERM_RARITY.get(t, _DEFAULT_RARITY)

    base = matched / max(1, len(terms))

    # ── Bigram bonus ───────────────────────────────────────────────
    bigrams = _query_bigrams(terms)
    if bigrams:
        bigram_matched = sum(1 for bg in bigrams if bg in text)
        base += W["bigram_match"] * bigram_matched / max(1, len(bigrams))

    # ── Rarity bonus ───────────────────────────────────────────────
    if matched > 0:
        rarity_bonus = (rarity_sum / matched - _DEFAULT_RARITY) * 0.15
        base += max(0.0, rarity_bonus)

    return base


def _query_category_match(query: str, paper: Dict[str, Any]) -> float:
    """Check whether query terms overlap with the paper's arXiv categories."""
    if not query:
        return 0.0
    # Map common domain names to arXiv category prefixes
    _DOMAIN_CATEGORY_MAP: Dict[str, List[str]] = {
        "computer vision": ["cs.cv"],
        "vision": ["cs.cv"],
        "image": ["cs.cv", "cs.gr", "eess.iv"],
        "video": ["cs.cv", "cs.mm"],
        "nlp": ["cs.cl"],
        "natural language": ["cs.cl"],
        "language model": ["cs.cl"],
        "llm": ["cs.cl", "cs.ai"],
        "machine learning": ["cs.lg", "stat.ml"],
        "deep learning": ["cs.lg", "cs.ai", "cs.ne"],
        "reinforcement": ["cs.lg", "cs.ai"],
        "security": ["cs.cr"],
        "crypto": ["cs.cr"],
        "privacy": ["cs.cr"],
        "software": ["cs.se"],
        "programming": ["cs.pl", "cs.se"],
        "compiler": ["cs.pl"],
        "database": ["cs.db"],
        "network": ["cs.ni"],
        "distributed": ["cs.dc"],
        "robotics": ["cs.ro"],
        "hci": ["cs.hc"],
        "human computer": ["cs.hc"],
        "graphics": ["cs.gr"],
        "visualization": ["cs.gr"],
        "theory": ["cs.cc", "cs.ds"],
        "algorithm": ["cs.ds"],
        "architecture": ["cs.ar"],
        "hardware": ["cs.ar"],
        "operating": ["cs.os"],
        "formal": ["cs.fl", "cs.lo"],
    }
    query_lower = query.lower()
    paper_categories = _paper_value(paper.get("categories") or paper.get("primary_category")).lower()
    if not paper_categories:
        return 0.0

    matched_categories: set = set()
    for domain, cats in _DOMAIN_CATEGORY_MAP.items():
        if domain in query_lower:
            matched_categories.update(cats)

    if not matched_categories:
        return 0.0

    for cat in matched_categories:
        if cat in paper_categories:
            return SCORING_WEIGHTS["category_match"]
    return 0.0


def _field_aware_citation_divisor(paper: Dict[str, Any]) -> float:
    """Return a field-adjusted citation divisor based on arXiv category.

    High-citation fields (AI/ML/CV) use a larger divisor so the score doesn't
    saturate.  Lower-citation fields (theory, crypto) use a smaller divisor
    so meaningful papers still get credit.
    """
    categories = _paper_value(paper.get("categories") or paper.get("primary_category")).lower()
    # High-citation CS subfields
    _high_cite = {"cs.ai", "cs.lg", "cs.cv", "cs.cl", "cs.ne", "stat.ml"}
    # Moderate-citation
    _mod_cite = {"cs.cr", "cs.se", "cs.db", "cs.ni", "cs.dc", "cs.hc",
                 "cs.mm", "cs.si", "cs.ir", "cs.gr"}
    # Lower-citation
    _low_cite = {"cs.pl", "cs.lo", "cs.fl", "cs.cc", "cs.ds", "cs.it",
                 "cs.sc", "cs.cg", "cs.dm", "cs.gl", "math."}

    base = SCORING_WEIGHTS["citation_divisor"]
    if any(c in categories for c in _high_cite):
        return base  # default 200
    if any(c in categories for c in _mod_cite):
        return base * 0.5  # 100
    if any(c in categories for c in _low_cite):
        return base * 0.25  # 50
    return base * 0.7  # 140 — unknown field


def _paper_score(paper: Dict[str, Any], query: str = "") -> float:
    """Compute a relevance / quality score for a paper record.

    The score combines:
    - **Query relevance**: title, abstract, keyword, and category matching
    - **Provenance**: DOI, PDF availability, arXiv source
    - **Download capability**: direct/indirect PDF access
    - **Source reliability**: aggregated from source metadata
    - **Temporal**: smooth year decay
    - **Impact**: field-adjusted citation count
    - **Prestige**: venue tier recognition

    All weights are configurable via ``SCORING_WEIGHTS`` / environment variables.
    """
    W = SCORING_WEIGHTS  # shorthand
    score = 0.0
    sources = _paper_sources(paper)

    # ── Query relevance ──────────────────────────────────────────────
    if query:
        query_terms = _query_terms(query)
        if query_terms:
            # Title match (highest weight)
            title = _normalize_lookup_text(paper.get("title"))
            score += W["title_term_match"] * _query_term_match_ratio(query_terms, title)
            # Abstract match (medium weight)
            abstract = _normalize_lookup_text(
                paper.get("abstract") or paper.get("summary") or ""
            )
            if abstract:
                score += W["abstract_term_match"] * _query_term_match_ratio(query_terms, abstract)
            # Keyword match (low weight — keywords are high-precision)
            keywords = _normalize_lookup_text(paper.get("keywords"))
            if keywords:
                score += W["keyword_term_match"] * _query_term_match_ratio(query_terms, keywords)
            # Category match from query domain terms
            score += _query_category_match(query, paper)

    # ── Provenance ───────────────────────────────────────────────────
    if _paper_value(paper.get("doi")).strip():
        score += W["doi_present"]
    if _paper_has_pdf_signal(paper):
        score += W["pdf_signal"]
    if _env_bool(PREFER_ARXIV_ENV, True) and "arxiv" in sources:
        score += W["arxiv_source"]

    # ── Download capability ──────────────────────────────────────────
    if sources:
        best_download = _best_source_download_capability(sources)
        if best_download is True:
            score += W["direct_download"]
        elif best_download and best_download != "record_dependent":
            score += W["oa_download"]

    # ── Source signals ───────────────────────────────────────────────
    score += min(W["source_reliability"], _paper_source_reliability_score(paper) / 100.0)
    score += min(len(sources), int(W["max_source_count"])) * W["source_count"]

    # ── Temporal ─────────────────────────────────────────────────────
    year = _paper_year_number(paper)
    if year:
        current_year = _dt.datetime.now().year
        score += W["year_max"] / (1.0 + max(0, current_year - year) / W["year_decay_rate"])

    # ── Impact (field-adjusted citations) ────────────────────────────
    citations = _paper_citations(paper)
    if citations:
        divisor = _field_aware_citation_divisor(paper)
        score += min(W["citation_max"], citations / divisor)

    # ── Venue prestige ───────────────────────────────────────────────
    score += _venue_prestige_score(paper)

    return round(score, 4)


# =========================================================================
# 10.  Data-driven ranking profiles
# =========================================================================

# Canonical name for the built-in agent-skill profile (kept for backward
# compatibility with orchestration.py).
AGENT_SKILL_RANKING_PROFILE = "agent-skill"
AGENT_SKILL_PROFILE_ALIASES = {
    "agent-skill",
    "agent_skill",
    "agentskill",
    "skill-agent",
}


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


def _term_matches(text: str, term: str) -> bool:
    """Match a term using word boundaries for single-word terms.

    Multi-word terms (e.g. "language model") still use substring matching.
    This prevents "agent" from matching "reagent" and "tool" from matching
    "toolkit".
    """
    if " " in term:
        return term in text
    return bool(re.search(rf"\b{re.escape(term)}\b", text))


def _profile_score(paper: Dict[str, Any], spec: ProfileSpec) -> float:
    """Score a paper against a generic ``ProfileSpec``.

    Scoring logic (same for all profiles):
    1. **Boost phrases**: exact phrase matches → high bonus (+title extra)
    2. **Term groups**: papers matching terms from *all* non-empty groups get
       a pair bonus; papers matching only some groups get a smaller bonus.
    3. **Negative phrases**: exact phrase matches → penalty (+title extra)
    4. **Category boost**: arXiv category prefix match → small bonus
    5. **Non-downloadable penalty**: papers without PDF access are demoted
       (but kept in candidate pool for fallback).

    All weights come from ``SCORING_WEIGHTS``.
    """
    W = SCORING_WEIGHTS
    text = _normalize_lookup_text(_paper_profile_text(paper))
    title = _normalize_lookup_text(paper.get("title"))
    if not text:
        return 0.0

    score = 0.0

    # ── 1. Boost phrases ────────────────────────────────────────────
    for phrase in spec.boost_phrases:
        if phrase in text:
            score += W["profile_boost_phrase"]
            if phrase in title:
                score += W["profile_boost_title"]

    # ── 2. Term groups (cross-group pair bonus) ─────────────────────
    group_matches: Dict[str, bool] = {}
    for group_name, terms in spec.term_groups.items():
        if not terms:
            group_matches[group_name] = False
            continue
        group_matches[group_name] = any(
            _term_matches(text, term) for term in terms
        )

    active_groups = [g for g, matched in group_matches.items() if matched]
    if len(active_groups) >= 2:
        # All specified groups matched → full pair bonus
        pair_weight = sum(
            spec.term_group_weights.get(g, W["profile_term_pair"])
            for g in active_groups
        ) / len(active_groups)
        score += pair_weight
    elif len(active_groups) == 1:
        # Only one group matched → reduced bonus
        single_weight = spec.term_group_weights.get(
            active_groups[0], W["profile_term_single"]
        )
        score += single_weight

    # ── 3. Negative phrases ─────────────────────────────────────────
    for phrase in spec.negative_phrases:
        if phrase in text:
            score -= W["profile_negative"]
            if phrase in title:
                score -= W["profile_negative_title"]

    # ── 4. Category boost ───────────────────────────────────────────
    if spec.category_boost:
        paper_cats = _paper_value(
            paper.get("categories") or paper.get("primary_category")
        ).lower()
        if paper_cats:
            for cat in spec.category_boost:
                if cat.lower() in paper_cats:
                    score += W["category_match"]
                    break

    # ── 5. Non-downloadable penalty ─────────────────────────────────
    if paper.get("download_ready") is False:
        score -= W["profile_non_downloadable"]

    return round(score, 4)


def _agent_skill_profile_score(paper: Dict[str, Any]) -> float:
    """Backward-compatible wrapper for agent-skill profile scoring.

    Delegates to ``_profile_score`` with the registered ``agent-skill`` spec.
    Used by ``orchestration.py`` for query auto-detection.
    """
    spec = get_profile("agent-skill")
    if spec is None:
        return 0.0
    return _profile_score(paper, spec)


def _ranking_profile_name(ranking_profile: str = "") -> str:
    """Resolve a ranking profile string to its canonical name.

    Returns the canonical name if the profile is registered, otherwise
    returns the raw value (which may be empty or an unknown name).
    """
    profile = (ranking_profile or "").strip().lower()
    spec = get_profile(profile)
    if spec is not None:
        return spec.name
    return profile


def _rank_papers_for_profile(
    papers: List[Dict[str, Any]],
    *,
    ranking_profile: str = "",
    query: str = "",
) -> List[Dict[str, Any]]:
    """Re-score and sort papers using a named ranking profile.

    When *ranking_profile* matches a registered ``ProfileSpec``, each paper
    receives a ``profile_score`` from ``_profile_score`` that is added to
    its base score.  Papers are then sorted by (combined_score, profile_score,
    download_ready) descending.

    Unregistered profile names pass through unchanged (no re-ranking).
    """
    spec = get_profile(ranking_profile)
    if spec is None:
        return list(papers)

    ranked: List[Dict[str, Any]] = []
    for paper in papers:
        item = dict(paper)
        base_score = float(item.get("score") or _paper_score(item, query=query))
        profile_score = _profile_score(item, spec)
        item["ranking_profile"] = spec.name
        item["profile_score"] = round(profile_score, 4)
        item["score"] = round(base_score + profile_score, 4)
        ranked.append(item)

    # Two-phase sort: profile_score (relevance) first, then download capability
    # breaks ties within relevance tiers.  This prevents a downloadable but
    # irrelevant paper from outranking a highly relevant metadata-only paper.
    ranked.sort(
        key=lambda paper: (
            float(paper.get("profile_score") or 0),
            1 if paper.get("download_ready") else 0,
            float(paper.get("score") or 0),
        ),
        reverse=True,
    )
    return ranked


# ── Register built-in profiles ──────────────────────────────────────────

# Agent-skill profile (LLM agent skill / tool-use / skill-library papers)
register_profile(ProfileSpec(
    name="agent-skill",
    aliases={"agent_skill", "agentskill", "skill-agent"},
    description="LLM agent skill, tool-use, skill library/retrieval/security",
    boost_phrases=[
        "agent skill", "agent skills", "agentic skill",
        "skill library", "skill libraries", "skill retrieval",
        "skill ecosystem", "skill security", "skill audit", "skill revision",
        "skillbench", "skillsbench", "skill.md",
        "llm agent", "llm agents", "language agent", "software agent",
        "tool-using agent", "tool using agent",
    ],
    term_groups={
        "agent": {"agent", "agents", "agentic", "llm", "language model", "tool"},
        "skill": {"skill", "skills", "capability", "capabilities"},
    },
    negative_phrases=[
        "human skill", "human skills", "motor skill", "social skill",
        "piano skill", "clinical skill", "surgical skill", "teaching skill",
        "workforce skill", "reinforcement learning",
        "robotic skill", "robotic skills", "robot skill", "robot skills",
        "chemistry skill", "chemistry skills", "chemical skill", "chemical skills",
        "nursing skill", "nursing skills",
        "sport skill", "sport skills", "sports skill", "sports skills",
        "athletic skill", "athletic skills",
        "embodied agent", "embodied agents",
        "physical skill", "physical skills",
    ],
))

# ── Domain-specific CS profiles ─────────────────────────────────────────

# Computer Vision
register_profile(ProfileSpec(
    name="cv",
    aliases={"computer-vision", "computer_vision", "vision"},
    description="Computer vision: image/video understanding, generation, detection, segmentation",
    boost_phrases=[
        "computer vision", "image recognition", "object detection",
        "image segmentation", "semantic segmentation", "instance segmentation",
        "image generation", "image synthesis", "image classification",
        "visual recognition", "visual understanding", "scene understanding",
        "pose estimation", "depth estimation", "optical flow",
        "image restoration", "image super-resolution", "image inpainting",
        "generative adversarial", "diffusion model", "stable diffusion",
        "vision transformer", "convolutional neural", "feature extraction",
        "multi-view", "structure from motion", "slam",
    ],
    term_groups={
        "vision": {
            "image", "images", "video", "videos", "visual", "vision",
            "pixel", "pixels", "photograph", "camera", "scene",
        },
        "technique": {
            "detection", "segmentation", "recognition", "classification",
            "generation", "reconstruction", "tracking", "registration",
            "rendering", "super-resolution",
        },
    },
    negative_phrases=[
        "medical image", "medical imaging", "mri", "ct scan", "ultrasound",
        "x-ray", "radiology", "histopathology",
    ],
    category_boost={"cs.cv", "cs.gr", "eess.iv"},
))

# Natural Language Processing
register_profile(ProfileSpec(
    name="nlp",
    aliases={"natural-language-processing", "natural_language_processing"},
    description="NLP: language models, text generation, translation, summarization, QA",
    boost_phrases=[
        "natural language", "language model", "language models",
        "large language model", "text generation", "text classification",
        "machine translation", "neural machine translation",
        "question answering", "text summarization", "named entity",
        "sentiment analysis", "text mining", "information extraction",
        "dialogue system", "conversational agent", "chatbot",
        "word embedding", "token classification", "sequence labeling",
        "prompt engineering", "in-context learning", "few-shot learning",
        "retrieval augmented", "rag", "chain-of-thought",
    ],
    term_groups={
        "language": {
            "language", "text", "corpus", "token", "sentence", "word",
            "document", "paragraph", "discourse", "semantic", "syntax",
            "grammar", "linguistic", "translation", "multilingual",
        },
        "model": {
            "transformer", "bert", "gpt", "llama", "encoder", "decoder",
            "attention", "embedding", "fine-tun", "pretrain", "pre-train",
            "language model", "llm",
        },
    },
    negative_phrases=[
        "speech recognition", "speech synthesis", "audio", "acoustic",
        "phoneme", "prosody",
    ],
    category_boost={"cs.cl"},
))

# Machine Learning (broad — overlap with cv/nlp but more general)
register_profile(ProfileSpec(
    name="ml",
    aliases={"machine-learning", "machine_learning", "deep-learning", "deep_learning"},
    description="Machine learning: architectures, training methods, optimization, theory",
    boost_phrases=[
        "machine learning", "deep learning", "neural network",
        "gradient descent", "backpropagation", "stochastic gradient",
        "loss function", "activation function", "convolutional layer",
        "transformer architecture", "attention mechanism",
        "self-supervised", "unsupervised learning", "semi-supervised",
        "transfer learning", "meta learning", "continual learning",
        "federated learning", "distributed training",
        "model compression", "knowledge distillation", "pruning",
        "quantization", "neural architecture search",
        "bayesian neural", "variational inference", "normalizing flow",
        "score-based model", "energy-based model",
    ],
    term_groups={
        "learning": {
            "learning", "training", "optimization", "gradient", "loss",
            "stochastic", "convergence", "regularization", "dropout",
            "batch normalization", "layer normalization",
        },
        "model": {
            "neural network", "deep network", "transformer", "cnn", "rnn",
            "lstm", "resnet", "mlp", "autoencoder", "gan",
            "diffusion", "encoder", "decoder", "attention",
        },
    },
    negative_phrases=[
        "reinforcement learning", "multi-armed bandit", "q-learning",
        "policy gradient", "markov decision",
    ],
    category_boost={"cs.lg", "cs.ai", "stat.ml", "cs.ne"},
))

# Security / Cryptography
register_profile(ProfileSpec(
    name="security",
    aliases={"cybersecurity", "crypto", "cryptography", "infosec"},
    description="Security & cryptography: attacks, defenses, protocols, privacy, formal verification",
    boost_phrases=[
        "side-channel", "side channel", "fault attack", "power analysis",
        "timing attack", "cache attack", "spectre", "meltdown",
        "return-oriented programming", "control-flow integrity",
        "fuzzing", "symbolic execution", "taint analysis",
        "zero-knowledge proof", "homomorphic encryption", "secure multi-party",
        "differential privacy", "adversarial example", "backdoor attack",
        "membership inference", "model inversion", "data poisoning",
        "blockchain security", "smart contract", "formal verification",
        "intrusion detection", "malware detection", "anomaly detection",
        "access control", "authentication protocol", "key exchange",
    ],
    term_groups={
        "security": {
            "security", "attack", "defense", "adversary", "adversarial",
            "vulnerability", "exploit", "threat", "malware", "privacy",
            "cryptographic", "encryption", "decryption", "authentication",
            "protocol", "trusted", "trust",
        },
        "technique": {
            "verification", "analysis", "detection", "prevention",
            "mitigation", "protection", "audit", "forensic",
        },
    },
    negative_phrases=[
        "network security policy", "security policy compliance",
        "organizational security", "security awareness",
    ],
    category_boost={"cs.cr"},
))

# Systems
register_profile(ProfileSpec(
    name="systems",
    aliases={"system", "os", "operating-systems", "distributed-systems"},
    description="Systems: OS, distributed systems, networking, databases, storage",
    boost_phrases=[
        "operating system", "distributed system", "file system",
        "virtual memory", "cache coherence", "memory management",
        "process scheduling", "concurrency control", "transaction processing",
        "consensus protocol", "paxos", "raft", "distributed consensus",
        "fault tolerance", "replication", "consistency model",
        "software-defined network", "network function virtualization",
        "congestion control", "packet scheduling", "load balancing",
        "key-value store", "column store", "graph database",
        "query optimization", "index structure", "join algorithm",
    ],
    term_groups={
        "system": {
            "system", "kernel", "distributed", "network", "storage",
            "database", "server", "cluster", "cloud", "datacenter",
            "virtualization", "container", "microservice",
        },
        "property": {
            "performance", "scalability", "reliability", "availability",
            "consistency", "durability", "latency", "throughput",
            "fault-tolerant", "fault tolerance", "replication",
        },
    },
    negative_phrases=[
        "biological system", "ecosystem", "nervous system",
        "power system", "energy system",
    ],
    category_boost={"cs.os", "cs.dc", "cs.ni", "cs.db", "cs.ar"},
))

# Software Engineering
register_profile(ProfileSpec(
    name="se",
    aliases={"software-engineering", "software_engineering", "programming"},
    description="Software engineering: testing, verification, program analysis, DevOps, requirements",
    boost_phrases=[
        "software engineering", "program analysis", "static analysis",
        "dynamic analysis", "software testing", "test generation",
        "mutation testing", "regression testing", "fault localization",
        "program repair", "automated program repair", "code review",
        "software verification", "model checking", "abstract interpretation",
        "type system", "program synthesis", "programming language",
        "compiler optimization", "just-in-time compilation",
        "continuous integration", "devops", "software architecture",
        "api design", "refactoring", "technical debt",
    ],
    term_groups={
        "software": {
            "software", "program", "code", "source code", "compiler",
            "interpreter", "debug", "testing", "verification",
            "development", "engineering",
        },
        "technique": {
            "analysis", "synthesis", "generation", "repair", "optimization",
            "refactoring", "checking", "proving", "inference",
        },
    },
    negative_phrases=[
        "social software", "educational software", "enterprise software management",
    ],
    category_boost={"cs.se", "cs.pl"},
))

# Robotics
register_profile(ProfileSpec(
    name="robotics",
    aliases={"robot", "robotic"},
    description="Robotics: planning, control, perception, manipulation, SLAM",
    boost_phrases=[
        "robot manipulation", "motion planning", "path planning",
        "trajectory optimization", "grasp planning", "grasp synthesis",
        "robot learning", "imitation learning", "reinforcement learning",
        "sim-to-real", "sim2real", "domain randomization",
        "inverse kinematics", "dynamics model", "contact model",
        "state estimation", "sensor fusion", "lidar", "point cloud",
        "autonomous navigation", "obstacle avoidance",
        "human-robot interaction", "human robot interaction",
        "soft robot", "modular robot", "swarm robot",
    ],
    term_groups={
        "robot": {
            "robot", "robotic", "manipulator", "gripper", "end-effector",
            "drone", "uav", "autonomous vehicle", "autonomous driving",
        },
        "technique": {
            "planning", "control", "perception", "localization",
            "mapping", "slam", "tracking", "navigation", "grasping",
            "manipulation", "kinematic", "dynamic",
        },
    },
    negative_phrases=[
        "robot skill", "robot skills",  # excluded from agent-skill
    ],
    category_boost={"cs.ro"},
))

# Human-Computer Interaction
register_profile(ProfileSpec(
    name="hci",
    aliases={"human-computer-interaction", "human_computer_interaction", "ux", "ui"},
    description="HCI: user interfaces, interaction design, accessibility, VR/AR, visualization",
    boost_phrases=[
        "human-computer interaction", "user interface", "user experience",
        "interaction design", "participatory design", "design thinking",
        "usability study", "user study", "heuristic evaluation",
        "cognitive model", "information visualization", "visual analytics",
        "augmented reality", "virtual reality", "mixed reality",
        "haptic feedback", "gesture recognition", "eye tracking",
        "accessible design", "inclusive design", "universal design",
        "mobile interaction", "wearable computing", "tangible interface",
        "collaborative system", "computer-supported cooperative",
    ],
    term_groups={
        "human": {
            "user", "human", "participant", "person", "people",
            "interaction", "interface", "design", "experience",
            "usability", "accessibility",
        },
        "technology": {
            "display", "screen", "mobile", "wearable", "tangible",
            "virtual", "augmented", "haptic", "gesture", "touch",
            "voice", "multimodal",
        },
    },
    negative_phrases=[
        "human subject regulation", "institutional review",
    ],
    category_boost={"cs.hc"},
))

# Theory / Algorithms
register_profile(ProfileSpec(
    name="theory",
    aliases={"theoretical-cs", "algorithms", "complexity"},
    description="Theoretical CS: algorithms, complexity, data structures, graph theory, logic",
    boost_phrases=[
        "approximation algorithm", "randomized algorithm", "online algorithm",
        "graph algorithm", "streaming algorithm", "sublinear algorithm",
        "np-complete", "np-hard", "polynomial time", "exponential time",
        "circuit complexity", "communication complexity", "query complexity",
        "data structure", "hash table", "balanced tree", "skip list",
        "graph theory", "matching problem", "flow network",
        "linear programming", "semidefinite programming",
        "combinatorial optimization", "discrete optimization",
        "computational geometry", "convex hull", "voronoi diagram",
    ],
    term_groups={
        "theory": {
            "algorithm", "complexity", "bound", "theorem", "proof",
            "lemma", "graph", "combinatorial", "polynomial",
            "approximation", "optimal", "lower bound", "upper bound",
        },
        "structure": {
            "data structure", "tree", "hash", "queue", "stack",
            "heap", "array", "matrix", "set", "map",
        },
    },
    negative_phrases=[
        "empirical evaluation", "experimental results", "real-world dataset",
    ],
    category_boost={"cs.ds", "cs.cc", "cs.cg", "cs.dm", "cs.gt"},
))

# Computer Graphics
register_profile(ProfileSpec(
    name="graphics",
    aliases={"computer-graphics", "rendering", "animation"},
    description="Computer graphics: rendering, geometry, animation, simulation, visualization",
    boost_phrases=[
        "global illumination", "ray tracing", "path tracing",
        "radiance field", "neural radiance", "nerf", "gaussian splatting",
        "geometry processing", "mesh generation", "mesh simplification",
        "shape analysis", "surface reconstruction", "point cloud processing",
        "character animation", "motion capture", "physics simulation",
        "fluid simulation", "cloth simulation", "particle system",
        "procedural generation", "texture synthesis",
        "computational photography", "image-based rendering",
    ],
    term_groups={
        "graphics": {
            "rendering", "shading", "lighting", "texture", "mesh",
            "geometry", "surface", "shape", "animation", "simulation",
        },
        "technique": {
            "tracing", "splatting", "rasterization", "reconstruction",
            "deformation", "sampling", "filtering", "compression",
        },
    },
    negative_phrases=[
        "graphic design", "visual design", "typography",
    ],
    category_boost={"cs.gr"},
))


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
_TITLE_SIMILARITY_DEDUP_THRESHOLD = _scoring_weight("DEDUP_TITLE_SIMILARITY", 0.85)
# Author/venue diversity caps (0 = disabled, N = max papers per first-author / venue)
_DIVERSITY_MAX_PER_AUTHOR = int(_scoring_weight("DIVERSITY_MAX_PER_AUTHOR", 3))
_DIVERSITY_MAX_PER_VENUE = int(_scoring_weight("DIVERSITY_MAX_PER_VENUE", 0))
_DIVERSITY_SCORE_PENALTY = _scoring_weight("DIVERSITY_PENALTY", 1.5)


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

    # ── Diversity rerank ───────────────────────────────────────────────
    # Prevent a single author or venue from dominating the top results.
    # Uses MMR-style (Maximal Marginal Relevance) greedy selection:
    # papers already selected for the shortlist penalise remaining papers
    # that share the same first-author or venue.
    if _DIVERSITY_MAX_PER_AUTHOR > 0 or _DIVERSITY_MAX_PER_VENUE > 0:
        deduped = _diversity_rerank(deduped)
    return deduped


def _first_author_key(paper: Dict[str, Any]) -> str:
    """Normalised first-author surname for diversity grouping."""
    authors = _paper_value(paper.get("authors"))
    if not authors:
        return ""
    first = authors.split(",")[0].strip()
    # Take the last word as surname: "John Smith" -> "smith"
    parts = first.split()
    return parts[-1].lower() if parts else first.lower()


def _venue_key(paper: Dict[str, Any]) -> str:
    """Normalised venue name for diversity grouping."""
    venue = _paper_field(paper, "venue") or _paper_field(paper, "journal") or ""
    return _normalize_venue_name(venue)


def _diversity_rerank(
    papers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """MMR-style diversity reranking to avoid author/venue clustering.

    Greedy algorithm:
    1. Take the highest-scored paper (unchanged).
    2. For each remaining paper, compute a diversity penalty based on how many
       already-selected papers share the same first-author or venue.
    3. Re-sort by (score - penalty) and take the next best.
    4. Repeat until all papers are placed.

    Perf: O(N²) where N is the paper count.  For N≤100 this is sub-millisecond.
    """
    if len(papers) <= 1:
        return papers

    remaining = list(papers)
    result: List[Dict[str, Any]] = []
    author_counts: Dict[str, int] = {}
    venue_counts: Dict[str, int] = {}

    while remaining:
        best_idx = 0
        best_effective_score = float("-inf")

        for idx, paper in enumerate(remaining):
            base_score = float(paper.get("score") or 0)
            penalty = 0.0

            if _DIVERSITY_MAX_PER_AUTHOR > 0:
                author = _first_author_key(paper)
                if author:
                    count = author_counts.get(author, 0)
                    excess = count - _DIVERSITY_MAX_PER_AUTHOR + 1
                    if excess > 0:
                        penalty += _DIVERSITY_SCORE_PENALTY * excess * excess

            if _DIVERSITY_MAX_PER_VENUE > 0:
                venue = _venue_key(paper)
                if venue and venue not in ("arxiv", "arxiv.org", "preprint", ""):
                    count = venue_counts.get(venue, 0)
                    excess = count - _DIVERSITY_MAX_PER_VENUE + 1
                    if excess > 0:
                        penalty += _DIVERSITY_SCORE_PENALTY * excess * excess

            effective = base_score - penalty
            if effective > best_effective_score:
                best_effective_score = effective
                best_idx = idx

        chosen = remaining.pop(best_idx)
        result.append(chosen)

        # Update counts
        author = _first_author_key(chosen)
        if author:
            author_counts[author] = author_counts.get(author, 0) + 1
        venue = _venue_key(chosen)
        if venue and venue not in ("arxiv", "arxiv.org", "preprint", ""):
            venue_counts[venue] = venue_counts.get(venue, 0) + 1

    return result


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
