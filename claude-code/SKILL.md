---
name: paper-search
description: MCP-first academic paper workflow. Use when the user asks to find papers, search academic literature, download PDFs, parse papers with MinerU, inspect parsed paper caches, or build a small research corpus.
---

# Paper Search

Use the `paper-search-mcp` MCP tools first. Do not open a terminal, activate
`.venv`, or run `uv run` for normal paper search/download/parse requests when
MCP tools are available.

The CLI is only a fallback when the MCP server/tools are unavailable or the
user explicitly asks for command-line usage.

## MCP-First Workflow

For natural-language requests, prefer one high-level tool:

- `paper_research_workflow(query, intent="search_download_parse", count=5, max_results_per_source=5, sources="", year=None, ranking_profile="", selection_mode="auto_top", selected_indices="", save_path="~/Desktop/papers", use_scihub=False, parse_mode="auto", backend="", force=False, parse_execution="background", download_concurrency=0)`

This is the default entry point for requests like:

- "Find recent papers about X"
- "Download the top 5 papers about X"
- "Find and parse papers about X"
- "Build me a small corpus on X"

Default behavior:

1. Search with the configured fast profile unless sources are provided.
2. Rank and persist a selection session.
3. Download the top `count` papers unless the request is search-only or manual selection.
4. If parsing is requested, submit a background parse job via MCP and return `parse_job.job_id`.
5. Return PDF paths, parse prompt/session metadata, and the next MCP tool to call.

Use `parse_execution="sync"` only when the user explicitly wants to wait in the
current call. Otherwise keep `parse_execution="background"` so the MCP call
returns quickly.

Do not pass `save_path` unless the user explicitly requests a custom directory.
The MCP default resolves to `~/Desktop/papers`.

## Intent Mapping

- Search only: call `paper_research_workflow(intent="search_only")`.
- Search with user choice: call `paper_research_workflow(intent="search_only", selection_mode="manual")`, then use the returned checkbox App or numbered fallback.
- Download top papers: call `paper_research_workflow(intent="search_download", count=<n>)`.
- Download and parse top papers: call `paper_research_workflow(intent="search_download_parse", count=<n>, parse_execution="background")`.
- Parse existing local PDFs: call `parse_pdf_with_mineru` or `parse_pdfs_with_mineru`; do not use the CLI.
- Check parse progress: call `get_parse_job_status(job_id)`.
- Read parsed results: call `list_parsed_papers`, `get_parsed_paper`, `search_parsed_papers`, `get_paper_assets`, or `get_parsed_paths`.

## Selection UI

For hosts with MCP Apps, use the returned `app` field or call:

- `render_paper_selection_app(selection_token, ...)`

For hosts without MCP Apps, show the returned `numbered_fallback` and pass the
user's indices to:

- `download_selected_papers(selection_token, selected_indices="1,3")`
- `submit_parse_job(selection_token, selected_indices="all")`
- `parse_selected_papers(selection_token, selected_indices="all")` only when synchronous parsing is desired.

If a user asks to choose papers before downloading, do not auto-download. Return
the selection UI/fallback first.

## MinerU Setup

Use MCP tools for configuration and health checks:

- `mineru_setup_status()`
- `render_mineru_api_key_setup_app(reason="missing")`
- `configure_mineru_api_key(api_key)`
- `mineru_health_check(mode="auto")`

If parsing returns `mineru_api_key_prompt`, surface the MCP App/config prompt.
Do not ask the user to edit shell profiles or activate a virtual environment.

## Lower-Level MCP Tools

Use these only when the high-level workflow is not specific enough:

- `search_papers(query, max_results_per_source=5, sources="", year=None)`
- `crawl_papers_for_selection(query, max_results_per_source=5, sources="", year=None, ranking_profile="")`
- `search_papers_with_elicitation(query, max_results_per_source=5, sources="", year=None, save_path="~/Desktop/papers", use_scihub=False, mode="auto", backend="", force=False)`
- `search_papers_for_parsing(query, max_results_per_source=5, sources="", year=None)`
- `download_selected_papers(selection_token, selected_indices="all", save_path="~/Desktop/papers", use_scihub=False, concurrency=0)`
- `submit_parse_job(selection_token, selected_indices="all", save_path="~/Desktop/papers", use_scihub=False, mode="auto", backend="", force=False)`
- `get_parse_job_status(job_id)`
- `list_parse_jobs()`
- `cancel_parse_job(job_id)`
- `parse_pdf_with_mineru(pdf_path, paper_key="", source="", paper_id="", doi="", title="", mode="auto", backend="", force=False)`
- `parse_pdfs_with_mineru(pdf_paths, mode="auto", backend="", force=False)`

## CLI Fallback Only

Use the CLI only if MCP tools are unavailable or the user explicitly requests a
terminal command.

All CLI commands run via:

```bash
uv run --directory <REPO_PATH> paper-search <command> [args]
```

Examples:

```bash
uv run --directory <REPO_PATH> paper-search search "<query>" -n 5 -s arxiv,semantic,openalex
uv run --directory <REPO_PATH> paper-search parse <pdf_path> --paper-key <key> --mode auto
uv run --directory <REPO_PATH> paper-search cache list
```

Never activate `.venv` just to use the CLI fallback; `uv run --directory` is
self-contained.

## Environment

Parser/cache settings use the same `.env` convention as the MCP server:

```dotenv
PAPER_SEARCH_MCP_CACHE_DIR=.paper_search_cache
PAPER_SEARCH_MCP_MINERU_MODE=auto
PAPER_SEARCH_MCP_MINERU_BASE_URL=http://127.0.0.1:8000
PAPER_SEARCH_MCP_MINERU_BACKEND=pipeline
PAPER_SEARCH_MCP_MINERU_API_KEY=
PAPER_SEARCH_MCP_MINERU_EXTRACT_BASE_URL=https://mineru.net/api/v4
PAPER_SEARCH_MCP_MINERU_MODEL_VERSION=vlm
PAPER_SEARCH_MCP_MINERU_LANGUAGE=ch
PAPER_SEARCH_MCP_MINERU_IS_OCR=false
PAPER_SEARCH_MCP_MINERU_ENABLE_FORMULA=true
PAPER_SEARCH_MCP_MINERU_ENABLE_TABLE=true
```
