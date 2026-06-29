# paper_search_mcp/engine/search.py
"""
Core search engine functions extracted from server.py.

Provides:
- Environment variable helpers (_env_int, _env_float)
- Search result caching (_search_cache_key, _cached_search_result, _store_search_result)
- async_search adapter for synchronous searchers
- Source management (_disabled_sources, _parse_sources)
- Source capability reporting (_source_config_status, _source_capability_report)
- Timed source search wrapper (_search_source_with_timeout)
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from typing import Any, Dict, List, Optional

from ..config import get_env

# ---------------------------------------------------------------------------
# Environment variable name constants
# ---------------------------------------------------------------------------
SEARCH_PROFILE_ENV = "SEARCH_PROFILE"
SEARCH_CACHE_TTL_ENV = "SEARCH_CACHE_TTL_SECONDS"
SEARCH_SOURCE_TIMEOUT_ENV = "SEARCH_SOURCE_TIMEOUT_SECONDS"
SEARCH_TIMEOUT_ENV = "SEARCH_TIMEOUT_SECONDS"
DISABLED_SOURCES_ENV = "DISABLED_SOURCES"

# ---------------------------------------------------------------------------
# In-memory search result cache
# ---------------------------------------------------------------------------
SEARCH_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _split_env_csv(value: str) -> List[str]:
    """Split a comma-separated environment variable value into trimmed parts."""
    return [item.strip() for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# _env_int / _env_float
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an integer environment variable with a minimum bound."""
    raw = get_env(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Read a float environment variable with a minimum bound."""
    raw = get_env(name, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


# ---------------------------------------------------------------------------
# Search cache
# ---------------------------------------------------------------------------
def _search_cache_key(
    query: str,
    max_results_per_source: int,
    sources: str,
    year: Optional[str],
    requested_count: int = 0,
) -> str:
    """Produce a deterministic cache key for a search request.

    *requested_count* is included when > 0 so that progressive retry
    rounds with different oversampling targets produce distinct keys.
    """
    resolved_sources = ",".join(_parse_sources(sources))
    obj: Dict[str, Any] = {
        "query": query,
        "max_results_per_source": max_results_per_source,
        "sources": resolved_sources,
        "year": year or "",
    }
    if requested_count > 0:
        obj["requested_count"] = requested_count
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _cached_search_result(cache_key: str) -> Optional[Dict[str, Any]]:
    """Return a cached search payload if it exists and is within TTL."""
    ttl = _env_int(SEARCH_CACHE_TTL_ENV, 300, minimum=0)
    if ttl <= 0:
        return None
    entry = SEARCH_RESULT_CACHE.get(cache_key)
    if not entry:
        return None
    if time.time() - float(entry.get("stored_at", 0)) > ttl:
        SEARCH_RESULT_CACHE.pop(cache_key, None)
        return None
    payload = copy.deepcopy(entry.get("payload") or {})
    if payload:
        payload["cache"] = {"hit": True, "ttl_seconds": ttl}
    return payload


def _store_search_result(cache_key: str, payload: Dict[str, Any]) -> None:
    """Persist a search result payload in the in-memory LRU-bounded cache."""
    ttl = _env_int(SEARCH_CACHE_TTL_ENV, 300, minimum=0)
    if ttl <= 0:
        return
    papers = payload.get("papers", [])
    if not papers and payload.get("timed_out_sources"):
        return
    # ── LRU eviction: keep at most 128 entries ──────────────────────
    _MAX_CACHE_SIZE = 128
    if len(SEARCH_RESULT_CACHE) >= _MAX_CACHE_SIZE:
        oldest = min(
            SEARCH_RESULT_CACHE,
            key=lambda k: SEARCH_RESULT_CACHE[k].get("stored_at", 0),
        )
        SEARCH_RESULT_CACHE.pop(oldest, None)
    SEARCH_RESULT_CACHE[cache_key] = {
        "stored_at": time.time(),
        "payload": copy.deepcopy(payload),
    }


# ---------------------------------------------------------------------------
# async_search - adapter for synchronous searchers
# ---------------------------------------------------------------------------
async def async_search(searcher, query: str, max_results: int, **kwargs) -> List[Dict]:
    """Run a synchronous searcher in a thread and return paper dicts."""
    if "year" in kwargs:
        papers = await asyncio.to_thread(
            searcher.search, query, max_results=max_results, year=kwargs["year"]
        )
    elif kwargs:
        papers = await asyncio.to_thread(
            searcher.search, query, max_results=max_results, **kwargs
        )
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [paper.to_dict() for paper in papers]


# ---------------------------------------------------------------------------
# Source registry data
# ---------------------------------------------------------------------------
ALL_SOURCES = [
    "arxiv",
    "pubmed",
    "biorxiv",
    "medrxiv",
    "google_scholar",
    "iacr",
    "semantic",
    "crossref",
    "openalex",
    "pmc",
    "core",
    "europepmc",
    "dblp",
    "openaire",
    "citeseerx",
    "doaj",
    "base",
    "zenodo",
    "hal",
    "ssrn",
    "unpaywall",
]

FAST_SOURCES = [
    "arxiv",
    "semantic",
    "openalex",
    "crossref",
    "pubmed",
    "pmc",
    "europepmc",
]

PDF_CS_SOURCES = [
    "arxiv",
    "openalex",
    "crossref",
    "dblp",
]

AGENT_SKILL_FAST_SOURCES = [
    "arxiv",
    "openalex",
    "crossref",
]

AGENT_SKILL_BROAD_SOURCES = [
    "arxiv",
    "openalex",
    "crossref",
    "semantic",
    "google_scholar",
]

DEEP_SOURCES = list(ALL_SOURCES)

SEARCH_PROFILES: Dict[str, List[str]] = {
    "fast": FAST_SOURCES,
    "default": FAST_SOURCES,
    "pdf-cs": PDF_CS_SOURCES,
    "cs-pdf": PDF_CS_SOURCES,
    "pdf_cs": PDF_CS_SOURCES,
    "cs_pdf": PDF_CS_SOURCES,
    "agent-skill-fast": AGENT_SKILL_FAST_SOURCES,
    "agent_skill_fast": AGENT_SKILL_FAST_SOURCES,
    "agent-skill-broad": AGENT_SKILL_BROAD_SOURCES,
    "agent_skill_broad": AGENT_SKILL_BROAD_SOURCES,
    "deep": DEEP_SOURCES,
    "all": ALL_SOURCES,
}

SOURCE_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "arxiv": {"search": True, "download": True, "read": True, "notes": "Open API; reliable PDF/read."},
    "pubmed": {"search": True, "download": False, "read": False, "notes": "Metadata only; use DOI/PMC fallback."},
    "biorxiv": {"search": True, "download": True, "read": True, "notes": "Recent category-filtered preprints."},
    "medrxiv": {"search": True, "download": True, "read": True, "notes": "Recent category-filtered preprints."},
    "google_scholar": {"search": True, "download": False, "read": False, "notes": "Discovery only; bot-detection prone."},
    "iacr": {"search": True, "download": True, "read": True, "notes": "IACR ePrint PDFs."},
    "semantic": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Works when an openAccessPdf URL is available."},
    "crossref": {"search": True, "download": False, "read": False, "notes": "DOI and metadata backbone."},
    "openalex": {"search": True, "download": False, "read": False, "notes": "Metadata and OA links; does not host PDFs."},
    "pmc": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Open-access PMC PDFs."},
    "core": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "CORE key recommended."},
    "europepmc": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Biomedical OA PDFs when available."},
    "dblp": {"search": True, "download": False, "read": False, "notes": "Computer science metadata only."},
    "openaire": {"search": True, "download": False, "read": False, "notes": "OA discovery links; direct tool is metadata-oriented."},
    "citeseerx": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "Upstream availability varies."},
    "doaj": {"search": True, "download": "url_dependent", "read": "url_dependent", "notes": "Open-access journal records."},
    "base": {"search": "institution_dependent", "download": "record_dependent", "read": "record_dependent", "notes": "OAI-PMH may need registered IP."},
    "zenodo": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "Open repository files."},
    "hal": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "HAL documents with public PDF."},
    "ssrn": {"search": "best_effort", "download": "public_pdf_only", "read": "public_pdf_only", "notes": "SSRN bot/login restrictions vary."},
    "unpaywall": {"search": "doi_lookup", "download": False, "read": False, "notes": "OA URL resolver; requires email."},
}

SOURCE_RELIABILITY_SCORES: Dict[str, Dict[str, Any]] = {
    "arxiv": {
        "score": 95,
        "tier": "primary_pdf",
        "cs_relevant": True,
        "pdf_first": True,
        "notes": "Official CS-heavy preprint source with stable PDF identifiers.",
    },
    "openalex": {
        "score": 82,
        "tier": "oa_discovery",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Broad metadata and OA link discovery; often points back to arXiv PDFs.",
    },
    "crossref": {
        "score": 72,
        "tier": "doi_backbone",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "DOI metadata backbone; PDF links are useful but record-dependent.",
    },
    "dblp": {
        "score": 68,
        "tier": "cs_metadata",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "High-quality CS bibliography used for DOI/title enrichment; no hosted PDFs.",
    },
    "semantic": {
        "score": 48,
        "tier": "conditional_oa_pdf",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Useful when openAccessPdf is present; unauthenticated requests are rate-limited.",
    },
    "iacr": {
        "score": 42,
        "tier": "conditional_pdf",
        "cs_relevant": True,
        "pdf_first": True,
        "notes": "Cryptography-focused PDFs; current smoke test found direct download blocked.",
    },
    "core": {
        "score": 38,
        "tier": "repository",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Repository records are PDF-dependent and benefit from an API key.",
    },
    "citeseerx": {
        "score": 28,
        "tier": "legacy_repository",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Legacy CS source; current access path is timeout-prone.",
    },
    "google_scholar": {
        "score": 20,
        "tier": "discovery_only",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "Discovery only and anti-bot prone; not suitable for automated PDF retrieval.",
    },
    "pmc": {"score": 50, "tier": "biomedical_oa_pdf", "cs_relevant": False, "pdf_first": False},
    "europepmc": {"score": 46, "tier": "biomedical_oa_pdf", "cs_relevant": False, "pdf_first": False},
    "biorxiv": {"score": 44, "tier": "life_science_preprint", "cs_relevant": False, "pdf_first": True},
    "medrxiv": {"score": 42, "tier": "medical_preprint", "cs_relevant": False, "pdf_first": True},
    "pubmed": {"score": 32, "tier": "biomedical_metadata", "cs_relevant": False, "pdf_first": False},
    "zenodo": {"score": 45, "tier": "repository", "cs_relevant": False, "pdf_first": False},
    "hal": {"score": 43, "tier": "repository", "cs_relevant": False, "pdf_first": False},
    "openaire": {"score": 40, "tier": "oa_discovery", "cs_relevant": False, "pdf_first": False},
    "doaj": {"score": 35, "tier": "journal_directory", "cs_relevant": False, "pdf_first": False},
    "base": {"score": 34, "tier": "repository", "cs_relevant": False, "pdf_first": False},
    "unpaywall": {"score": 36, "tier": "doi_oa_resolver", "cs_relevant": False, "pdf_first": False},
    "ssrn": {"score": 24, "tier": "social_science", "cs_relevant": False, "pdf_first": False},
    "ieee": {
        "score": 62,
        "tier": "cs_metadata",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "High-quality EE/CS metadata (CVPR, ICCV, TPAMI, etc.); requires API key;"
        " no public PDF download (institutional access needed).",
    },
    "acm": {
        "score": 55,
        "tier": "cs_metadata",
        "cs_relevant": True,
        "pdf_first": False,
        "notes": "ACM DL metadata (SIGGRAPH, SIGCOMM, etc.); requires API key;"
        " no public PDF download (institutional access needed).",
    },
}

SOURCE_CONFIG_KEYS: Dict[str, List[str]] = {
    "semantic": ["PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"],
    "core": ["PAPER_SEARCH_MCP_CORE_API_KEY", "CORE_API_KEY"],
    "unpaywall": ["PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "UNPAYWALL_EMAIL"],
    "zenodo": ["PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN", "ZENODO_ACCESS_TOKEN"],
    "openaire": ["PAPER_SEARCH_MCP_OPENAIRE_API_KEY", "OPENAIRE_API_KEY"],
    "ieee": ["PAPER_SEARCH_MCP_IEEE_API_KEY", "IEEE_API_KEY"],
    "acm": ["PAPER_SEARCH_MCP_ACM_API_KEY", "ACM_API_KEY"],
    "mineru": ["PAPER_SEARCH_MCP_MINERU_API_KEY", "MINERU_API_KEY"],
}


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------
def _disabled_sources() -> set[str]:
    """Return the set of source names disabled via environment variable."""
    return set(_split_env_csv(get_env(DISABLED_SOURCES_ENV, "").lower()))


def _source_reliability(source: str) -> Dict[str, Any]:
    """Return the PDF-first reliability metadata for a source."""
    key = str(source or "").strip().lower()
    data = copy.deepcopy(SOURCE_RELIABILITY_SCORES.get(key, {}))
    data.setdefault("score", 0)
    data.setdefault("tier", "unknown")
    data.setdefault("cs_relevant", False)
    data.setdefault("pdf_first", False)
    return data


def _source_reliability_score(source: str) -> float:
    """Return a numeric PDF-first reliability score for a source."""
    try:
        return float(_source_reliability(source).get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _rank_sources_by_reliability(sources: List[str]) -> List[str]:
    """Sort sources by PDF-first reliability while preserving ties."""
    indexed = list(enumerate(sources))
    indexed.sort(
        key=lambda item: (
            _source_reliability_score(item[1]),
            -item[0],
        ),
        reverse=True,
    )
    return [source for _, source in indexed]


def _parse_sources(sources: str) -> List[str]:
    """
    Resolve a sources string into a list of enabled source names.

    Accepts profile aliases (fast, deep, all, agent-skill-fast, etc.) or a
    comma-separated list of source names.  Falls back to the SEARCH_PROFILE
    environment variable or 'fast'.
    """
    value = (sources or "").strip().lower()
    if not value:
        value = get_env(SEARCH_PROFILE_ENV, "fast").strip().lower() or "fast"

    if value in SEARCH_PROFILES:
        candidates = [
            source for source in SEARCH_PROFILES[value] if source in ALL_SOURCES
        ]
    else:
        candidates = [
            part.strip().lower() for part in value.split(",") if part.strip()
        ]

    disabled = _disabled_sources()
    return [
        source
        for source in candidates
        if source in ALL_SOURCES and source not in disabled
    ]


def _source_config_status(source: str) -> Dict[str, Any]:
    """Report which configuration keys for a source are set in the environment."""
    keys = SOURCE_CONFIG_KEYS.get(source, [])
    configured = []
    for key in keys:
        normalized = key
        legacy = key.removeprefix("PAPER_SEARCH_MCP_")
        value = os.environ.get(normalized)
        if value is None:
            value = os.environ.get(legacy)
        if value:
            configured.append(key)
    return {
        "keys": keys,
        "configured": bool(configured),
        "configured_keys": configured,
    }


def _source_capability_report(source: str) -> Dict[str, Any]:
    """Produce a capability / configuration report for a single source."""
    capability = copy.deepcopy(SOURCE_CAPABILITIES.get(source, {}))
    key_status = _source_config_status(source)
    download = capability.get("download")
    recommendation = "enabled"
    reason = ""

    if source in _disabled_sources():
        recommendation = "disabled_by_env"
        reason = f"Disabled by PAPER_SEARCH_MCP_{DISABLED_SOURCES_ENV}."
    elif download is False:
        recommendation = "metadata_only"
        reason = (
            "This source does not provide direct PDFs; use it for discovery "
            "and route DOI/arXiv IDs through fallback/download tools."
        )
    elif source == "semantic" and not key_status["configured"]:
        recommendation = "optional_key_missing"
        reason = (
            "Search works unauthenticated with lower rate limits; downloads "
            "only work when Semantic Scholar exposes openAccessPdf."
        )
    elif source == "unpaywall" and not key_status["configured"]:
        recommendation = "configure_key"
        reason = "Unpaywall requires a contact email for DOI OA lookup."
    elif source == "core" and not key_status["configured"]:
        recommendation = "configure_key"
        reason = "CORE has limited functionality without an API key."
    elif source == "ssrn":
        recommendation = "use_for_metadata_only"
        reason = (
            "SSRN downloads are best-effort and often blocked by "
            "login/anti-bot restrictions."
        )
    elif source == "zenodo" and not key_status["configured"]:
        recommendation = "public_ok"
        reason = (
            "Public Zenodo records and open PDFs do not require a token; "
            "a token only improves limits/restricted-record access."
        )

    return {
        "source": source,
        "available": source in ALL_SOURCES,
        "disabled": source in _disabled_sources(),
        "capabilities": capability,
        "reliability": _source_reliability(source),
        "config": key_status,
        "recommendation": recommendation,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Timed source search
# ---------------------------------------------------------------------------
async def _search_source_with_timeout(
    source: str,
    operation: Any,
    timeout_seconds: float,
) -> Dict[str, Any]:
    """Run a per-source search operation with an optional timeout."""
    started = time.perf_counter()
    try:
        if timeout_seconds > 0:
            output = await asyncio.wait_for(operation, timeout=timeout_seconds)
        else:
            output = await operation
        return {
            "source": source,
            "output": output or [],
            "error": "",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except asyncio.TimeoutError:
        return {
            "source": source,
            "output": [],
            "error": f"timed out after {timeout_seconds:g}s",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "timed_out": True,
        }
    except Exception as exc:
        return {
            "source": source,
            "output": [],
            "error": str(exc),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
