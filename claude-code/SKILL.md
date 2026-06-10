---
name: paper-search
description: Search, download, and read academic papers from 20+ sources (arXiv, PubMed, Semantic Scholar, CrossRef, etc). Use when the user asks to find papers, search for research, look up academic literature, download a paper PDF, or extract text from a paper.
---

# Paper Search

Search, download, and read academic papers via the `paper-search` CLI.

## CLI Usage

All commands run via:
```bash
uv run --directory <REPO_PATH> paper-search <command> [args]
```

Replace `<REPO_PATH>` with the absolute path to your clone of this repository.

### Search
```bash
uv run --directory <REPO_PATH> paper-search search "<query>" -n <max_per_source> -s <sources> -y <year>
```
- `-n`: results per source (default: 5)
- `-s`: comma-separated sources or "all" (default: all)
- `-y`: year filter for Semantic Scholar (e.g. "2020", "2018-2022")

For speed, prefer targeted sources (`-s arxiv,semantic,crossref`) over "all" unless broad coverage is needed.

### Download PDF
```bash
uv run --directory <REPO_PATH> paper-search download <source> <paper_id> [-o ~/Desktop]
```

### Read (extract text)
```bash
uv run --directory <REPO_PATH> paper-search read <source> <paper_id> [-o ~/Desktop]
```

### Parse PDF (MinerU-first, pypdf fallback)
```bash
uv run --directory <REPO_PATH> paper-search parse <pdf_path> --paper-key <key> --mode auto
```
- `--mode auto`: with `PAPER_SEARCH_MCP_MINERU_API_KEY`, try MinerU official extract API first; then local MinerU API, MinerU CLI, and pypdf fallback
- `--mode extract`: force MinerU official extract API only
- `--mode pypdf`: force lightweight PDF text extraction
- `--force`: re-parse even if cached
- The parse command also writes a same-name result zip beside the PDF, e.g. `paper.pdf` -> `paper.zip`

### MCP Elicitation Selection

For MCP clients with elicitation support, prefer:

- `search_papers_with_elicitation(query, max_results_per_source=5, sources="all", year=None, save_path="~/Desktop", use_scihub=False, mode="auto", backend="", force=False)`

This searches papers, asks the client to show a native multi-select form, then
parses the selected papers with the MinerU pipeline. In VS Code Copilot Agent
Mode this can appear as a multi-select control; the exact checkbox/dropdown
appearance is controlled by the client.

The same prompt is triggered after any MCP download/read tool saves a PDF. In
elicitation-capable clients the server asks for selected PDFs immediately; in
plain clients the tool result includes `parse_prompt.selection_token` plus a
numbered paper list for `parse_selected_papers`.

### MCP Numbered Selection Fallback

If the MCP client cannot provide elicitation UI, use the backend session flow:

1. Call MCP tool `search_papers_for_parsing`.
2. Show the returned numbered `papers` list to the user.
3. Ask the user to choose indices such as `1,3,5`, `2-4`, or `all`.
4. Call MCP tool `parse_selected_papers` with the returned `selection_token` and selected indices.

Useful MCP tools for this flow:
- `search_papers_with_elicitation(query, max_results_per_source=5, sources="all", year=None, save_path="~/Desktop", use_scihub=False, mode="auto", backend="", force=False)`
- `search_papers_for_parsing(query, max_results_per_source=5, sources="all", year=None)`
- `parse_selected_papers(selection_token, selected_indices="all", save_path="~/Desktop", use_scihub=False, mode="auto", backend="", force=False)`
- `list_search_sessions()`
- `get_search_session(selection_token)`
- `delete_search_session(selection_token)`

### Parsed Cache
```bash
uv run --directory <REPO_PATH> paper-search cache list
uv run --directory <REPO_PATH> paper-search cache get <paper_key> -f markdown
uv run --directory <REPO_PATH> paper-search cache search <paper_key> "<query>"
uv run --directory <REPO_PATH> paper-search cache assets <paper_key>
uv run --directory <REPO_PATH> paper-search cache paths <paper_key>
```

### Parser Health
```bash
uv run --directory <REPO_PATH> paper-search mineru-health
```

### List sources
```bash
uv run --directory <REPO_PATH> paper-search sources
```

## Output

`search` and `download` return JSON. `read` returns plain text. Config warnings go to stderr and can be ignored.

## Sources

arxiv, pubmed, biorxiv, medrxiv, google_scholar, iacr, semantic, crossref, openalex, pmc, core, europepmc, dblp, openaire, citeseerx, doaj, base, zenodo, hal, ssrn, unpaywall

Optional (env vars): ieee (`IEEE_API_KEY`), acm (`ACM_API_KEY`)

## Workflow

1. Search with targeted sources to find papers
2. Present results as a table: title, authors, year, source, DOI/URL
3. For MCP clients with elicitation support, use `search_papers_with_elicitation`
4. For MCP clients without elicitation UI, use `search_papers_for_parsing`, ask for numbered selections, then call `parse_selected_papers`
5. If the user wants a single PDF, use `download <source> <paper_id>` and report the saved path
6. If the user wants agent-ready full text from a local file, use `parse <pdf_path>` and then `cache get <paper_key>`
7. If MinerU API token is configured, prefer `--mode auto` or `--mode extract`; otherwise use `--mode pypdf` as a fallback

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
