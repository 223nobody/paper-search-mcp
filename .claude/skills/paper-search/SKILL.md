---
name: paper-search
description: MCP-first academic paper workflow. Use when the user asks to find papers, search academic literature, download PDFs, parse papers with MinerU, inspect parsed paper caches, or build a small research corpus.
---

# Paper Search

## ⚡ MCP FIRST — Read Before Anything Else

**Always use the `paper-search-mcp` MCP server tools.** This is the primary
interface. Every operation — search, download, parse, cache — should go through
MCP tools. The MCP server:

- Is already configured in `.claude/mcp.json` and auto-starts with Claude Code
- Exposes 50+ tools with full type safety and structured output
- Manages selection sessions, background jobs, and checkbox widgets
- Enforces safety policies (large-batch confirmation, fallback chains)

```
User request → MCP tool → structured result → next MCP tool → ...
                  ↑                                    │
                  └──── NEVER skip to CLI ─────────────┘
```

### When CLI is permitted (and ONLY then)

1. MCP server failed to start or tools return connection errors.
2. User explicitly typed a `uv run paper-search ...` command.
3. You confirmed MCP unavailability by calling a tool and getting a transport error.

In all other cases, **use MCP tools**. Do not open a terminal, activate `.venv`,
or run `uv run paper-search`.

## Quick Reference: MCP Tools by Intent

| Intent | MCP Tool | Key Parameters |
|--------|----------|----------------|
| Full pipeline (search→download→parse) | `paper_research_workflow` | `query`, `intent="search_download_parse"`, `count` |
| Search + get pick list | `crawl_papers_for_selection` | `query`, `max_results_per_source`, `ranking_profile` |
| Download from selection | `download_selected_papers` | `selection_token`, `selected_indices` |
| Parse from selection (background) | `submit_parse_job` | `selection_token`, `selected_indices` |
| Parse from selection (synchronous) | `parse_selected_papers` | `selection_token`, `selected_indices` |
| Search only (no download) | `search_papers` | `query`, `sources`, `max_results_per_source` |
| Single paper download | `download_with_fallback` | `source`, `paper_id`, `doi`, `title` |
| Single PDF parse | `parse_pdf_with_mineru` | `pdf_path`, `mode="auto"` |
| Batch parse local PDFs | `parse_pdfs_with_mineru` | `pdf_paths`, `mode="auto"` |
| Check MinerU setup | `mineru_health_check` | `mode="auto"` |
| Configure MinerU API key | `configure_mineru_api_key` | `api_key` |
| Check background parse | `get_parse_job_status` | `job_id` |
| Read parsed results | `get_parsed_paper` / `search_parsed_papers` | `paper_key`, `query` |
| List/search parsed corpus | `list_parsed_papers` / `search_parsed_papers` | |
| Manage cache | `delete_parsed_cache` / `cleanup_redundant_cache_artifacts` | |

## Core Workflow (MCP)

```
MCP: crawl_papers_for_selection  →  MCP: user selects (checkbox or numbered)  →  MCP: download_selected_papers  →  MCP: submit_parse_job
```

1. **Search**: Call `crawl_papers_for_selection` with the user's query.
   - Use `ranking_profile="agent-skill"` for LLM/skill/library/security/agent topics.
   - For CS topics, prefer sources: `arxiv,semantic,dblp,crossref,openalex,europepmc`.
2. **Select**: Present the returned checkbox widget (`app`) or `numbered_fallback` to the user.
3. **Download**: Call `download_selected_papers(selection_token, selected_indices="1,3,5")`.
4. **Parse**: Call `submit_parse_job(selection_token, selected_indices="...")` for background parsing.

## ⚠️ Large-Batch Selection (>10 papers)

When `count > 10` (threshold: `AUTO_PARSE_SAVED_PDF_LIMIT=10`), the server returns
`status: "selection_required"` instead of auto-downloading. This is a safety
mechanism — **do not bypass it**.

The response will contain:
- `app` — an MCP Apps checkbox widget for interactive selection
- `numbered_fallback` — a numbered list usable in text-only clients
- `selection_token` — required for subsequent `download_selected_papers` calls
- `next_tool` — the recommended next MCP tool to call (usually `render_paper_selection_app`)

**Action**: Surface the checkbox or numbered list to the user. Wait for their
selection before proceeding to download. Never skip this step by falling back to
manual CLI downloads for >10 papers.

If the MCP client cannot render the widget, call:
- `render_paper_selection_app(selection_token)` — for MCP Apps hosts
- `open_paper_selection_page(selection_token)` — opens a browser fallback page

## Source Selection Guide

The server is configured with `PAPER_SEARCH_MCP_SEARCH_PROFILE=pdf-cs` in
`.claude/mcp.json`. Available profiles and source sets:

| Domain | Recommended Sources |
|--------|-------------------|
| CS / AI / ML | `arxiv,semantic,dblp,crossref,openalex` |
| Biomedicine | `pubmed,pmc,biorxiv,medrxiv,europepmc` |
| General / Cross-discipline | `crossref,openalex,semantic,core,doaj` |
| Cybersecurity | `iacr,arxiv` |
| Preprints only | `arxiv,biorxiv,medrxiv` |

Profiles: `fast` (default), `deep` (more sources), `all` (every configured source).

## Parse Strategy

- **Default**: `parse_execution="background"` — returns `job_id` immediately.
  Check progress with `get_parse_job_status(job_id)`.
- **When to use sync**: Only when the user explicitly needs results in the current
  call AND the batch is ≤10 papers. Use `parse_execution="sync"`.
- **Batch parse**: For multiple local PDFs, prefer `parse_pdfs_with_mineru` over
  calling `parse_pdf_with_mineru` individually.

## MinerU Setup

The auto-mode priority chain: `extract (cloud API)` → `local_api` → `cli` → `pypdf`.

If the user needs best quality parsing with MinerU extract:
1. Call `mineru_setup_status()` to check current state.
2. If `extract_api.ok: false`, guide the user to obtain an API key from https://mineru.net.
3. Call `configure_mineru_api_key(api_key)` to persist it to the project `.env`.
4. Verify with `mineru_health_check(mode="auto")`.

Without an API key, `pypdf` serves as the text-extraction fallback.

## CLI Fallback (⚠️ ONLY when MCP is unavailable)

> **Check MCP first.** Call any MCP tool (e.g. `diagnose_paper_sources`) before
> falling back to CLI. If the MCP tool returns a result — use MCP. Only proceed
> here if you get a transport error or the server is not running.

All commands run from the project root (`C:\code\paper-search-mcp`).

```bash
# IMPORTANT: Always set PYTHONIOENCODING on Windows to avoid GBK encoding errors
PYTHONIOENCODING=utf-8 uv run paper-search <command> [args]
```

### Search
```bash
PYTHONIOENCODING=utf-8 uv run paper-search search "<query>" -n 5 -s arxiv,semantic,dblp,crossref,openalex
```
Returns JSON to stdout. Filter for CS-relevant papers by `source` and `categories`.

### Download
```bash
PYTHONIOENCODING=utf-8 uv run paper-search download arxiv <paper_id> -o ~/Desktop/papers
```

### Parse (individual)
```bash
# Use forward slashes for Windows paths to avoid bash escape issues
PYTHONIOENCODING=utf-8 uv run paper-search parse "C:/Users/<user>/Desktop/papers/<id>.pdf" \
    --mode auto --paper-key "<id>" --source arxiv --paper-id "<id>" --title "<title>"
```

### Parse (batch) — avoid for now
The `parse-batch` command with newline-separated paths has encoding issues on
Windows bash. Use individual `parse` commands run in parallel instead.

### Cache
```bash
PYTHONIOENCODING=utf-8 uv run paper-search cache list
```

### Key CLI gotchas on Windows
- Always set `PYTHONIOENCODING=utf-8` to prevent GBK codec crashes.
- Use **forward slashes** in PDF paths (`C:/Users/...`), not backslashes.
- The `$'...'` bash syntax mangles paths containing `\2`, `\4`, etc. as escape
  sequences. Pass paths as regular quoted strings with forward slashes.
- For parallel downloads/parses, use separate `run_in_background` bash calls
  rather than trying to batch within a single command.

## Cache & Corpus

**MCP first:** Use `list_parsed_papers`, `search_parsed_papers(query)`, `get_parsed_paper(paper_key)`.

Parsed papers are stored in `.paper_search_cache/` with a SQLite FTS index.
CLI fallback (only if MCP unavailable):
```bash
PYTHONIOENCODING=utf-8 uv run paper-search cache list
```

## Environment

Configuration is in `.claude/mcp.json`:
```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\code\\paper-search-mcp", "-m", "paper_search_mcp.server"],
      "env": {
        "PAPER_SEARCH_MCP_SEARCH_PROFILE": "pdf-cs",
        "PAPER_SEARCH_MCP_MINERU_MODE": "auto"
      }
    }
  }
}
```

Parser/cache overrides via `.env`:
```dotenv
PAPER_SEARCH_MCP_MINERU_API_KEY=        # MinerU extract cloud API key
PAPER_SEARCH_MCP_CACHE_DIR=.paper_search_cache
```
