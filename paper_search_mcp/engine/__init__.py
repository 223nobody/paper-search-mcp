# paper_search_mcp/engine/__init__.py
"""
Core engine package — zero MCP dependencies.

Public API:
- paper:   Paper metadata extraction, deduplication, scoring, ranking
- search:  Search orchestration, caching, source management
- download: Download pipeline, OA fallback chain, PDF validation
- parse:   Parse decision logic, batch prompts, MinerU key management
- jobs:    Background parse job state machine

Common entry points:
    from paper_search_mcp.engine import (
        async_search,                        # Run a synchronous searcher in a thread
        _paper_parse_candidate,              # Build parse-candidate dict from paper record
        _download_source_pdf,                # Source-native PDF download
        _download_with_fallback_path,        # Full OA fallback chain download
        _parse_job_snapshot,                 # Get parse job state
    )
"""

from .paper import (
    _paper_parse_candidate, _paper_unique_key, _paper_score,
    _dedupe_papers, _paper_doi, _extract_arxiv_id,
    _paper_field, _paper_value, _paper_year,
    _safe_filename, _canonical_pdf_stem,
    _searcher_for_source, _source_from_identifier,
)
from .search import async_search
from .download import (
    _download_source_pdf, _download_with_fallback_path,
    _is_valid_pdf_file, _invalid_mcp_save_path,
    _wrap_save_path_methods, _download_from_url,
    _download_selected_session_paper,
)
from .parse import (
    _parse_selected_indices, _workflow_parse_execution_name,
    _selection_semantics_name, _build_paper_selection_schema,
    _prompt_parse_saved_pdfs, _after_saved_pdf,
)
from .jobs import _parse_job_snapshot, _run_parse_job
