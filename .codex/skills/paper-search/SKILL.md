---
name: paper-search
description: Academic paper research workflow. Use when asked to find papers, search 20+ academic sources (arxiv, pubmed, semantic scholar, dblp, crossref, etc.), download PDFs with fallback chains, parse papers to Markdown/JSON via MinerU, inspect parsed paper caches, or build a research corpus. Supports large-batch paper selection with checkbox UI for >10 papers.
---

# Paper Search Workflow

Always use the `paper-search-mcp` MCP server tools. The server exposes 50+ tools
across search, download, parse, and cache management. Codex discovers tool
signatures from MCP — focus on **which tool for which intent**, not how to call it.

## Tool Selection by Intent

| User wants to... | Primary tool | Notes |
|---|---|---|
| Search + download + parse in one call | `paper_research_workflow` | Default entry point; handles the full pipeline |
| Search and get a numbered pick list | `crawl_papers_for_selection` | Returns `selection_token` + checkbox widget |
| Download from a selection | `download_selected_papers` | Needs `selection_token` from crawl step |
| Parse downloaded PDFs (bg) | `submit_parse_job` | Returns `job_id`; check with `get_parse_job_status` |
| Parse downloaded PDFs (sync, small batches) | `parse_selected_papers` | Blocks until parsing completes |
| Search only, no download | `search_papers` | Returns JSON with deduplicated results |
| Download a single known paper | `download_with_fallback` | Legal-first fallback chain; opt-in Sci-Hub |
| Parse a local PDF file | `parse_pdf_with_mineru` | One PDF at a time; `parse_pdfs_with_mineru` for batch |
| Check MinerU setup | `mineru_health_check` | Reports extract/local/cli/pypdf availability |
| Inspect parsed cache | `list_parsed_papers` / `get_parsed_paper` / `search_parsed_papers` | Read parsed markdown/JSON |
| List available sources | `list_sources` / `diagnose_paper_sources` | Check API keys and source status |

## Core Workflow Pattern

```text
crawl_papers_for_selection  →  user selects via checkbox/numbered list  →  download_selected_papers  →  submit_parse_job
```

## Large-Batch Selection (>10 papers)

When more than 10 papers are requested, the MCP server enforces a selection step
before download. The response includes:
- A **checkbox widget** (`app` field) for interactive selection
- A **numbered fallback** for text-based selection

Guide the user through selection before calling `download_selected_papers`. Do not
auto-download when `status: "selection_required"` is returned.

## Parse Strategy

- Default to **background** parsing (`parse_execution="background"`): returns immediately with a `job_id`.
- Use **sync** parsing only when the user explicitly needs results in the same call.
- Check progress with `get_parse_job_status(job_id)` or list all with `list_parse_jobs()`.

## MinerU API Key

If the user needs official MinerU extract (best quality), walk them through:
1. `mineru_setup_status()` — check current state
2. `configure_mineru_api_key(api_key)` — persist to project `.env`
3. Without a key, `auto` mode falls back to pypdf for text extraction.

## Source Profiles

The server is configured with `PAPER_SEARCH_MCP_SEARCH_PROFILE=fast` (see
`.codex/config.toml`). Available profiles:
- `fast` — major sources (arxiv, semantic, crossref, dblp, openalex, europepmc)
- `deep` — adds specialized sources (pubmed, biorxiv, citeseerx, zenodo, etc.)
- `all` — every configured source
- Or pass a comma-separated list: `arxiv,semantic,dblp`

## Cache & Corpus Building

Parsed papers are indexed in a SQLite FTS database (`.paper_search_cache/`).
After parsing multiple papers on a topic:
- `search_parsed_papers(query)` — full-text search across all parsed papers
- `list_parsed_papers` — browse the parsed corpus
- `get_parsed_paper(paper_key, output_format="markdown")` — read one paper

## Environment

MCP server configuration is in `.codex/config.toml`. Parser settings use `.env`:

```toml
# .codex/config.toml (MCP server)
[mcp_servers.paper-search-mcp]
command = "uv"
args = ["run", "--directory", ".", "-m", "paper_search_mcp.server"]
env = { PAPER_SEARCH_MCP_SEARCH_PROFILE = "fast", PAPER_SEARCH_MCP_MINERU_MODE = "auto" }
```

Optional `.env` overrides for parser behavior:
```dotenv
PAPER_SEARCH_MCP_MINERU_API_KEY=        # MinerU cloud API key
PAPER_SEARCH_MCP_CACHE_DIR=.paper_search_cache
```
