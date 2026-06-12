<h1 align="center">Paper Search MCP</h1>

A Model Context Protocol (MCP) server for searching and downloading academic papers from multiple sources. The project follows a free-first strategy: prioritize open and public data sources, support optional API keys when they improve stability or coverage, and keep source-specific connectors extensible for advanced users.

![PyPI](https://img.shields.io/pypi/v/paper-search-mcp.svg) ![License](https://img.shields.io/badge/license-MIT-blue.svg) ![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
[![smithery badge](https://smithery.ai/badge/@openags/paper-search-mcp)](https://smithery.ai/server/@openags/paper-search-mcp)

<p align="center"><a href="README_CN.md">Chinese</a> | <a href="README.md">English</a></p>

---

## Table of Contents

- [Overview](#overview)
- [Project Principles](#project-principles)
- [Features](#features)
- [Source Strategy](#source-strategy)
- [Sci-Hub Notice](#sci-hub-notice)
- [Installation](#installation)
  - [Claude Code (Skill)](#claude-code-skill--recommended-for-claude-code-users)
  - [Method 1 — Smithery](#method-1--smithery-one-command-recommended-for-claude-desktop)
  - [Method 2 — uvx](#method-2--uvx-no-install-always-latest)
  - [Method 3 — uv](#method-3--uv-persistent-install)
  - [Method 4 — pip](#method-4--pip-standard-python-install)
  - [Method 5 — npx](#method-5--npx-via-smithery-cli-no-local-python-needed)
  - [Method 6 — Docker](#method-6--docker)
  - [Method 7 — Clone & run from source](#method-7--clone--run-from-source-development--recommended-for-macos-local)
  - [Environment Variables](#environment-variables-env-file)
- [Contributing](#contributing)
- [Demo](#demo)
- [Star History](#star-history)
- [License](#license)
- [TODO](#todo)
- [Acknowledgements](#acknowledgements)

---

## Overview

`paper-search-mcp` is a Python-based tool for searching and downloading academic papers from various platforms. It provides tools for searching papers, downloading PDFs, and extracting text, making it ideal for researchers and AI-driven workflows. It can be used as an MCP server (for Claude Desktop and other MCP clients) or as a Claude Code skill with a CLI interface.

## Project Principles

- **Free-First**: Public and open sources are the default roadmap. Paid or restricted sources are not the core direction of this project.
- **Optional API Keys**: API keys are supported only when they improve stability, rate limits, or metadata quality. The MCP should still be usable without them whenever possible.
- **LLM-Friendly Retrieval**: Search results should be standardized, deduplicated, and as complete as possible for downstream LLM workflows.
- **Source Transparency**: Different sources have different strengths. The MCP should make those tradeoffs explicit instead of pretending every source supports full-text retrieval.

---

## Features

- **Two-Layer Architecture**:
  - **Layer 1 (Unified Tooling)**: High-level `search_papers` for multi-source concurrent search & deduplication, and `download_with_fallback` relying on publisher open access links with sequential fallbacks.
  - **Layer 2 (Platform Connectors)**: Modular connectors for specific academic platforms (arXiv, PubMed, bioRxiv, Semantic Scholar, etc.) equipped with intelligent DOI extraction via regex text analysis or API fields.
- **Multi-Source Support**: Search and download papers from arXiv, PubMed, bioRxiv, medRxiv, Google Scholar, IACR ePrint Archive, Semantic Scholar, Crossref, OpenAlex, PubMed Central (PMC), CORE, Europe PMC, dblp, OpenAIRE, CiteSeerX, DOAJ, BASE, Zenodo, HAL, SSRN, Unpaywall (DOI lookup), and optional Sci-Hub workflows.
- **Standardized Output**: Papers are returned in a consistent dictionary format via the `Paper` class.
- **Free-First Design**: Open and public sources are prioritized before any optional commercial or restricted integrations.
- **Optional API-Key Enhancement**: Sources like Semantic Scholar can work better with a user-provided API key, but are not intended to force paid usage.
- **Discovery + Retrieval Workflow**: Google Scholar and Crossref can be used for discovery and DOI backfilling, while open repositories and publisher links are used for lawful full-text resolution where available.
- **OA-First Fallback Chain**: `download_with_fallback` now follows source-native download → OpenAIRE/CORE/Europe PMC/PMC discovery → Unpaywall DOI resolution → optional Sci-Hub. Sci-Hub fallback is opt-in.
- **MinerU-First Parsing Pipeline**: Local PDFs can be parsed into `full.md`, `content_list.json`, `manifest.json`, and extracted assets beside the source PDF. With `PAPER_SEARCH_MCP_MINERU_API_KEY` configured, `extract`/`cloud_api` mode can submit multiple PDFs through one MinerU batch; `auto` still falls back through local API/CLI and `pypdf`.
- **Saved-PDF Parsing Prompts + Selection UI**: Single download/read tools can still auto-parse small saved-PDF sets. Batch selection downloads return a parse prompt instead of blocking on MinerU; batches over 10 PDFs surface the MCP Apps checkbox selector, while plain clients receive a backend `selection_token` and numbered fallback list.
- **Fast Parsed-Paper Search**: Parsed blocks are indexed into `.paper_search_cache/parsed_index.sqlite3` with SQLite FTS when available, while file-based search remains the fallback.
- **Background Parsing Jobs**: Long selected-paper parses can be submitted with `submit_parse_job`, then tracked with `get_parse_job_status`, `list_parse_jobs`, and `cancel_parse_job`.
- **MCP Integration**: Compatible with MCP clients for LLM context enhancement.
- **Extensible Design**: Easily add new academic platforms by extending the `academic_platforms` module.

## MinerU Parsing Workflow

The project now separates discovery/download from parsing. A typical agent workflow is:

1. Use `search_papers` or `paper-search search` to discover candidate papers.
2. Use source-native download or `download_with_fallback` to obtain a PDF.
3. Parse the PDF with MinerU. This writes parsed artifacts beside the PDF
   (`example_mineru/full.md`, `content_list.json`, `manifest.json`, `assets/`).
   The project cache keeps only lightweight metadata and indexes, not a
   duplicate PDF or duplicate parsed content:

```bash
paper-search parse ~/Desktop/example.pdf --paper-key example --mode auto
paper-search parse-batch ~/Desktop/a.pdf ~/Desktop/b.pdf --mode extract
```

4. Reuse parsed artifacts by paper key. The cache commands resolve back to the
   PDF-side `*_mineru` directory when it exists:

```bash
paper-search cache list
paper-search cache get example -f markdown
paper-search cache search example "attention"
paper-search cache search-index "attention" --paper-key example
paper-search cache rebuild-index
paper-search cache assets example
```

Parser configuration:

```dotenv
PAPER_SEARCH_MCP_CACHE_DIR=.paper_search_cache
PAPER_SEARCH_MCP_SEARCH_PROFILE=fast
PAPER_SEARCH_MCP_SEARCH_TIMEOUT_SECONDS=18
PAPER_SEARCH_MCP_SEARCH_SOURCE_TIMEOUT_SECONDS=12
PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS=300
PAPER_SEARCH_MCP_PARSE_CONCURRENCY=3
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
PAPER_SEARCH_MCP_MINERU_AUTO_ORDER=extract,local_api,cli,pypdf
PAPER_SEARCH_MCP_MINERU_BATCH_PARSE=false
PAPER_SEARCH_MCP_MINERU_UPLOAD_CONCURRENCY=4
PAPER_SEARCH_MCP_MINERU_DOWNLOAD_CONCURRENCY=4
PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=false
```

Set `PAPER_SEARCH_MCP_MINERU_MODE=extract` to force MinerU official extract
API only. In `auto` mode, the extract API is tried first when an API key is
present; if it fails, the chain continues to local MinerU API/CLI and `pypdf`.
Use `PAPER_SEARCH_MCP_MINERU_AUTO_ORDER` to tune that order, for example
`local_api,extract,cli,pypdf` when you keep a local MinerU server warm.

Search defaults to the `fast` profile instead of every connector. Pass
`sources=deep` or `sources=all` when you want the slower long-tail sources.
`PAPER_SEARCH_MCP_SEARCH_TIMEOUT_SECONDS`,
`PAPER_SEARCH_MCP_SEARCH_SOURCE_TIMEOUT_SECONDS`, and
`PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS` control aggregate timeouts,
per-source timeouts, and short-lived query caching.
`PAPER_SEARCH_MCP_PARSE_CONCURRENCY` controls selected-paper parse concurrency.
Set `mode=extract`/`mode=cloud_api`, or set
`PAPER_SEARCH_MCP_MINERU_BATCH_PARSE=true` for `auto`, to use MinerU's true
multi-file extract batch path for selected-paper parsing. Upload and result-zip
download parallelism can be tuned with
`PAPER_SEARCH_MCP_MINERU_UPLOAD_CONCURRENCY` and
`PAPER_SEARCH_MCP_MINERU_DOWNLOAD_CONCURRENCY`.
Set `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true` to also generate a same-name zip
beside each parsed PDF.

### MCP-first natural-language workflow

For natural-language agents and MCP hosts, prefer the high-level
`paper_research_workflow` tool. It keeps the whole flow inside MCP instead of
asking the agent to open a terminal or interpret CLI instructions:

```json
{
  "tool": "paper_research_workflow",
  "arguments": {
    "query": "agentic spatial reasoning",
    "intent": "search_download_parse",
    "count": 5,
    "sources": "arxiv,semantic,openalex",
    "parse_execution": "background"
  }
}
```

Use `intent="search_only"` for discovery, `intent="search_download"` for PDF
retrieval, and `intent="search_download_parse"` when the user asks for parsed
paper content. By default, parsing is submitted with `submit_parse_job` and the
response includes `parse_job.job_id` plus `workflow.next_tool =
"get_parse_job_status"`.

Do not pass `save_path` unless the user explicitly requests a custom directory.
The MCP default resolves to `~/Desktop/papers`.

### MCP auto parsing, selection UI, and numbered fallback

MCP clients with elicitation support, such as VS Code Copilot Agent Mode, can
use `search_papers_with_elicitation` to show a native multi-select form after
searching. The server creates a search session, asks the client to collect the
selected papers, then runs `parse_selected_papers` automatically.

Download/read tools use a saved-PDF policy:

- If one tool call saves **10 PDFs or fewer**, the server automatically parses
  all saved parse-ready PDFs with MinerU. The returned `parse_prompt` has
  `interaction: "auto_parse_saved_pdfs"` and includes the `parse_selected_papers`
  summary.
- If one tool call saves **more than 10 PDFs**, the server returns a selection
  prompt instead of parsing immediately. MCP Apps-capable clients can render
  `ui://paper-search/paper-selection.html` with `render_paper_selection_app`.
  Plain clients receive a numbered `papers` list and `selection_token`.
- `download_selected_papers` is optimized for batch retrieval. It saves PDFs,
  writes a manifest, and returns `parse_prompt` with either a background
  `submit_parse_job` recommendation or a checkbox selector for large batches.
  It does not synchronously parse the downloaded batch.
- `crawl_download_parse_papers` is kept as a compatibility workflow. New
  natural-language hosts should prefer `paper_research_workflow`, which can
  also submit the background parse job directly.

For example, `download_arxiv` returns `pdf_path`, `pdf_paths`, and
`parse_prompt`. Small single-paper downloads parse automatically; large batches
use the selection flow.

Lower-level MCP flow:

```json
{
  "tool": "search_papers_for_parsing",
  "arguments": {
    "query": "agentic spatial reasoning",
    "sources": "arxiv,semantic,openalex",
    "max_results_per_source": 3
  }
}
```

If the client does not support elicitation, or the user cancels the form, the
tool returns the same `selection_token` and numbered `papers` list used by the
backend fallback workflow:

1. Call `search_papers_for_parsing`.
2. Present the returned numbered `papers` list to the user.
3. Ask the user to choose indices such as `1,3,5`, `2-4`, or `all`.
4. Call `submit_parse_job` for background parsing, or `parse_selected_papers`
   when you explicitly want to wait for parsing in the current call.

For hosts without MCP Apps or elicitation UI, `open_paper_selection_page` can
open a localhost checkbox page in the system browser. That page calls
`submit_parse_job` after the user submits the selection. The MCP server
cannot force a Codex/host built-in browser; it can only return the URL or ask
the operating system to open it.

Fallback MCP flow:

```json
{
  "tool": "search_papers_for_parsing",
  "arguments": {
    "query": "agentic spatial reasoning",
    "sources": "arxiv,semantic,openalex",
    "max_results_per_source": 3
  }
}
```

Then submit selected entries for background parsing:

```json
{
  "tool": "submit_parse_job",
  "arguments": {
    "selection_token": "search_20260610_abcdef12",
    "selected_indices": "1,3",
    "save_path": "~/Desktop/papers",
    "mode": "auto"
  }
}
```

Search sessions are stored under `.paper_search_cache/sessions/`. Use
`list_search_sessions`, `get_search_session`, and `delete_search_session` to
inspect or clean them. Parsed-paper cache entries store metadata/status and
point to the PDF-side `*_mineru` artifacts to avoid duplicate PDFs and duplicate
parsed content in `.paper_search_cache`.

Parsed content can be indexed or rebuilt explicitly with `index_parsed_cache`.
Downloads also keep lightweight method health stats in
`.paper_search_cache/download_health.json`; inspect them with
`get_download_health_stats` or `paper-search cache download-health`.

Local optimization checks can be run without network access:

```bash
uv run python scripts/bench_search_parse.py --pdf-count 8 --mode pypdf --force
```

The benchmark reports first-parse time, cache-hit parse time, FTS rebuild/search
time, legacy file-search time, and the measured speedups as JSON.

## Source Strategy

The long-term goal is not to depend on a single search engine, but to combine multiple free and public sources with clear roles:

- **Open metadata backbone**: Crossref, OpenAlex, Semantic Scholar, dblp, CiteSeerX, SSRN, Unpaywall (DOI-centric OA metadata).
- **Discipline-specific sources**: arXiv, PubMed, PubMed Central, Europe PMC, IACR.
- **Open-access full-text sources**: arXiv, PMC, CORE, OpenAIRE, DOAJ, BASE, Zenodo, HAL, publisher open-access links.
- **Discovery and DOI recovery**: Google Scholar can be useful for finding titles, versions, and DOI clues when other public metadata sources are incomplete.

Recommended free-first roadmap:

1. Keep current public sources stable.
2. Add OpenAlex as a broad free metadata source.
3. Add PubMed Central and Europe PMC for stronger biomedical full-text access.
4. Add CORE and OpenAIRE for repository-based open-access retrieval.
5. Use Google Scholar mainly as a discovery fallback, not as the primary canonical source.

## Platform Capability Matrix

This matrix reflects **verified live-integration results** from functional and end-to-end regression tests in this repository. Columns show the highest capability level observed under normal conditions.

| Platform           | Search           | Download              | Read                  | Notes                                                                                                        |
| ------------------ | ---------------- | --------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------ |
| arXiv              | ✅               | ✅                    | ✅                    | Open API; reliable                                                                                           |
| PubMed             | ✅               | ❌                    | ⚠️ info-only          | Open API; reliable                                                                                           |
| bioRxiv            | ✅               | ✅                    | ✅                    | Open API; reliable                                                                                           |
| medRxiv            | ✅               | ✅                    | ✅                    | Open API; reliable                                                                                           |
| Google Scholar     | ⚠️               | ❌                    | ❌                    | Bot-detection active; set `PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL`                                        |
| IACR               | ✅               | ✅                    | ✅                    | Open API; reliable                                                                                           |
| Semantic Scholar   | ✅               | ✅ (OA)               | ✅ (OA)               | Works without key (rate-limited); key improves limits; key rejection (403) retried automatically without key |
| Crossref           | ✅               | ❌                    | ⚠️ info-only          | Open API; reliable                                                                                           |
| OpenAlex           | ✅               | ❌                    | ⚠️ info-only          | Open API; reliable                                                                                           |
| PMC                | ✅               | ✅ (OA only)          | ✅ (OA only)          | OA PDFs only; direct download may be blocked by some proxy environments                                      |
| CORE               | ✅               | ✅ (record-dependent) | ✅ (record-dependent) | Free key recommended; connector retries with backoff and falls back to key-less on 401/403                   |
| Europe PMC         | ✅               | ✅ (OA)               | ✅ (OA)               | OA PDFs only; direct download may be blocked by some proxy environments                                      |
| dblp               | ✅               | ❌                    | ⚠️ info-only          | Open API; reliable                                                                                           |
| OpenAIRE           | ✅               | ❌                    | ❌                    | Open API; retries 3× with escalating request profiles on transient 403                                       |
| CiteSeerX          | ⚠️               | ✅ (record-dependent) | ⚠️                    | API endpoint intermittently unavailable / redirects to web archive                                           |
| DOAJ               | ✅               | ⚠️ (URL-dependent)    | ⚠️ (URL-dependent)    | PDF availability varies by article; free key raises rate limits                                              |
| BASE               | ⚠️               | ✅ (record-dependent) | ✅ (record-dependent) | OAI-PMH endpoint requires institutional IP registration; returns empty gracefully otherwise                  |
| Zenodo             | ✅               | ✅ (record-dependent) | ✅ (record-dependent) | Open API; reliable                                                                                           |
| HAL                | ✅               | ✅ (record-dependent) | ✅ (record-dependent) | Open API; reliable                                                                                           |
| SSRN               | ⚠️               | ⚠️ best-effort        | ⚠️ best-effort        | 403 bot-detection active; public PDF only                                                                    |
| Unpaywall          | ✅ (DOI lookup)  | ❌                    | ❌                    | **Requires** `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL`                                                              |
| Sci-Hub (optional) | ⚠️ fallback-only | ✅                    | ❌                    | Optional; unstable mirrors; user responsibility                                                              |
| **IEEE Xplore** 🔑 | 🚧 skeleton      | 🚧 skeleton           | 🚧 skeleton           | Requires `PAPER_SEARCH_MCP_IEEE_API_KEY` to activate                                                         |
| **ACM DL** 🔑      | 🚧 skeleton      | 🚧 skeleton           | 🚧 skeleton           | Requires `PAPER_SEARCH_MCP_ACM_API_KEY` to activate                                                          |

> ✅ = reliable in live tests. ⚠️ = works but subject to upstream instability or access restrictions. ❌ = not supported. 🔑 = key required. 🚧 = skeleton only.

---

## Credential & API Key Requirements

All keys are **optional** unless noted. Configure them in `.env` (preferred) or as shell exports.

| Environment Variable                        | Provider         | Required?                               | How to obtain                                                                                          |
| ------------------------------------------- | ---------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL`          | Unpaywall        | **Yes** (Unpaywall disabled without it) | Any valid email; register at [unpaywall.org](https://unpaywall.org/products/api)                       |
| `PAPER_SEARCH_MCP_CORE_API_KEY`             | CORE             | Recommended                             | Free at [core.ac.uk/services/api](https://core.ac.uk/services/api)                                     |
| `PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar | Optional                                | Free at [semanticscholar.org](https://www.semanticscholar.org/product/api) — improves rate limits      |
| `PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL` | Google Scholar   | Optional                                | Your HTTP/HTTPS proxy URL — bypasses bot-detection                                                     |
| `PAPER_SEARCH_MCP_DOAJ_API_KEY`             | DOAJ             | Optional                                | Free at [doaj.org](https://doaj.org/apply-for-api-key/) — raises hourly rate limit                     |
| `PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN`      | Zenodo           | Optional                                | Free at [zenodo.org](https://zenodo.org/account/settings/applications/) — required for private records |
| `PAPER_SEARCH_MCP_IEEE_API_KEY`             | IEEE Xplore      | **Required to activate**                | Free at [developer.ieee.org](https://developer.ieee.org/)                                              |
| `PAPER_SEARCH_MCP_ACM_API_KEY`              | ACM DL           | **Required to activate**                | See [libraries.acm.org/digital-library/acm-open](https://libraries.acm.org/digital-library/acm-open)   |

All variables follow the `PAPER_SEARCH_MCP_<NAME>` prefix scheme. Legacy names without the prefix (e.g. `CORE_API_KEY`, `UNPAYWALL_EMAIL`) are still supported for backward compatibility.

---

## Known Upstream Limitations

Some search failures are caused by external provider instability, not by bugs in this project:

| Source           | Symptom                        | Cause                                                   | Workaround                                                                                                                        |
| ---------------- | ------------------------------ | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Google Scholar   | Returns 0 results / empty HTML | Bot-detection (CAPTCHA)                                 | Set `PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL` to a proxy                                                                        |
| Semantic Scholar | 429 rate-limited responses     | Anonymous access rate limit                             | Set `PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY`; if key is rejected (403) connector automatically retries without key             |
| CORE             | 500 / timeout errors           | Unauthenticated rate limiting                           | Set `PAPER_SEARCH_MCP_CORE_API_KEY` (free); connector retries with exponential backoff and falls back to key-less on 401/403      |
| OpenAIRE         | Transient 403 responses        | IP-based session rate limiting                          | Connector retries 3× per profile, escalating: plain session → XML Accept header → raw `requests.get` with Mozilla UA              |
| CiteSeerX        | 404 via web archive redirect   | PSU endpoint intermittently redirects to archive        | No workaround; connector returns empty gracefully                                                                                 |
| BASE             | Search returns 0 results       | OAI-PMH endpoint requires institutional IP registration | Register at [base-search.net](https://www.base-search.net/about/en/) for API access; connector returns empty gracefully otherwise |
| SSRN             | HTTP 403                       | Bot-detection (Cloudflare)                              | No workaround; connector tries two endpoints and returns a clear message on failure                                               |
| PMC / Europe PMC | PDF download ProxyError        | Local proxy blocking direct HTTPS PDF download          | Disable proxy or use `download_with_fallback` instead                                                                             |
| Unpaywall        | Skipped entirely               | `UNPAYWALL_EMAIL` env var not set                       | Set `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` in `.env`                                                                                  |

## Optional Paid Platform Connectors (Phase 3)

IEEE Xplore and ACM Digital Library connectors are included as **opt-in skeletons**.
They are **disabled by default** — no API calls are made unless you explicitly configure the corresponding keys.

| Platform            | Env Var                         | Status                                                                     |
| ------------------- | ------------------------------- | -------------------------------------------------------------------------- |
| IEEE Xplore         | `PAPER_SEARCH_MCP_IEEE_API_KEY` | 🚧 skeleton — search registered, download/read raise `NotImplementedError` |
| ACM Digital Library | `PAPER_SEARCH_MCP_ACM_API_KEY`  | 🚧 skeleton — search registered, download/read raise `NotImplementedError` |

**How to enable:**

```bash
export PAPER_SEARCH_MCP_IEEE_API_KEY=<your_ieee_key>       # free key at https://developer.ieee.org/
export PAPER_SEARCH_MCP_ACM_API_KEY=<your_acm_key>         # see https://libraries.acm.org/digital-library
```

Once a key is set, the corresponding source is automatically added to `ALL_SOURCES` and its MCP tools (`search_ieee` / `search_acm`, `download_ieee` / `download_acm`, `read_ieee_paper` / `read_acm_paper`) are registered at server startup.

Without a key the connectors log a startup warning only — the rest of the server is unaffected.

## Free Source Expansion (Phase 4)

Three additional free-source connectors are now integrated into the MCP server:

- `zenodo`: Official Zenodo REST API connector (search + record-dependent PDF/read support).
- `hal`: HAL public API connector (search + record-dependent PDF/read support).
- `ssrn`: Discovery-first connector with hardened parser and best-effort download/read when a direct public PDF link is available.
- `unpaywall`: DOI-centric OA metadata source for standalone lookup (`search_unpaywall`) and fallback URL resolution.

SSRN integration remains compliance-first: it only attempts direct public PDF links exposed by SSRN pages. If login/restricted delivery is required, the connector returns a clear message instead of bypassing access controls.

## Sci-Hub Notice

Sci-Hub support can remain available as an optional connector for users who explicitly choose to enable it, but it should not be treated as the default or recommended full-text path.

- Availability is unstable and mirrors change frequently.
- Legal and policy risks vary by jurisdiction.
- README and tool descriptions should clearly state that users are responsible for enabling and using it.
- Open-access and publisher-permitted sources should be tried first whenever possible.

---

## Installation

Choose the method that best fits your workflow. All methods support the same [optional API keys](#credential--api-key-requirements).

---

### Claude Code (Skill) — MCP-first guidance for Claude Code users

Install the skill when you want Claude Code to recognize paper-search requests
automatically. The skill is MCP-first: when `paper-search-mcp` MCP tools are
available, it tells the agent to call `paper_research_workflow` and related MCP
tools instead of opening a terminal.

**Prerequisites**: [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview).

**Step 1 — Clone the repo:**

```bash
git clone https://github.com/openags/paper-search-mcp.git ~/paper-search-mcp
```

**Step 2 — Install the skill:**

```bash
mkdir -p ~/.claude/skills/paper-search
cp ~/paper-search-mcp/claude-code/SKILL.md ~/.claude/skills/paper-search/SKILL.md
```

**Step 3 — Confirm MCP-first behavior:**

The included `claude-code/SKILL.md` instructs the agent to use MCP tools first.
CLI examples are retained only as a fallback if MCP tools are unavailable or the
user explicitly requests terminal commands.

**Step 4 (optional) — Configure API keys:**

Create a `.env` file in the repo root for optional API keys (see [Environment Variables](#environment-variables-env-file)).

**That's it.** Next time you start Claude Code, just ask it to find papers. For example:

- "Find me recent papers on CRISPR base editing"
- "Search arxiv and semantic scholar for transformer attention mechanisms"
- "Download the PDF for arxiv paper 2106.12345"

When MCP is available, the default natural-language path is
`paper_research_workflow`. The `paper-search` CLI is a fallback only.

---

> **MCP Server Config file locations** (for methods below)
>
> - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
> - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
> - **Linux**: `~/.config/Claude/claude_desktop_config.json`

---

### Method 1 — Smithery (one-command, recommended for Claude Desktop)

```bash
npx -y @smithery/cli install @openags/paper-search-mcp --client claude
```

Smithery automatically writes the correct config block for you. No manual JSON editing needed.

---

### Method 2 — `uvx` (no install, always latest)

`uvx` runs the package directly from PyPI without a permanent install. Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
# Install uv (skip if already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> ⚠️ **macOS note**: `uvx` generated wrapper scripts rely on `realpath`, which is not included in macOS by default. If you see a `realpath: command not found` error, either install GNU coreutils (`brew install coreutils`) or use **Method 3 (`uv run`)** instead — it does not have this limitation.

**Claude Desktop config:**

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "uvx",
      "args": ["paper-search-mcp"],
      "env": {
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "your@email.com",
        "PAPER_SEARCH_MCP_CORE_API_KEY": "",
        "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "",
        "PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN": "",
        "PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL": "",
        "PAPER_SEARCH_MCP_IEEE_API_KEY": "",
        "PAPER_SEARCH_MCP_ACM_API_KEY": ""
      }
    }
  }
}
```

---

### Method 3 — `uv` (persistent install)

```bash
uv tool install paper-search-mcp
```

**Claude Desktop config:**

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "uv",
      "args": ["tool", "run", "paper-search-mcp"],
      "env": {
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "your@email.com",
        "PAPER_SEARCH_MCP_CORE_API_KEY": "",
        "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "",
        "PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN": "",
        "PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL": "",
        "PAPER_SEARCH_MCP_IEEE_API_KEY": "",
        "PAPER_SEARCH_MCP_ACM_API_KEY": ""
      }
    }
  }
}
```

---

### Method 4 — `pip` (standard Python install)

```bash
pip install paper-search-mcp
```

**Claude Desktop config:**

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "python",
      "args": ["-m", "paper_search_mcp.server"],
      "env": {
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "your@email.com",
        "PAPER_SEARCH_MCP_CORE_API_KEY": "",
        "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "",
        "PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN": "",
        "PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL": "",
        "PAPER_SEARCH_MCP_IEEE_API_KEY": "",
        "PAPER_SEARCH_MCP_ACM_API_KEY": ""
      }
    }
  }
}
```

> If `python` is not on your PATH, replace it with the full path (e.g. `/usr/bin/python3` or `C:\Python311\python.exe`). Run `which python3` / `where python` to find it.

---

### Method 5 — `npx` (via Smithery CLI, no local Python needed)

```bash
npx -y @smithery/cli run @openags/paper-search-mcp
```

**Claude Desktop config:**

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "npx",
      "args": ["-y", "@smithery/cli", "run", "@openags/paper-search-mcp"],
      "env": {
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "your@email.com",
        "PAPER_SEARCH_MCP_CORE_API_KEY": "",
        "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": ""
      }
    }
  }
}
```

---

### Method 6 — Docker

```bash
docker build -t paper-search-mcp .
docker run --rm -i \
  -e PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=your@email.com \
  -e PAPER_SEARCH_MCP_CORE_API_KEY=your_core_key \
  paper-search-mcp
```

**Claude Desktop config:**

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "paper-search-mcp"],
      "env": {
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "your@email.com",
        "PAPER_SEARCH_MCP_CORE_API_KEY": "",
        "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "",
        "PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN": "",
        "PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL": "",
        "PAPER_SEARCH_MCP_IEEE_API_KEY": "",
        "PAPER_SEARCH_MCP_ACM_API_KEY": ""
      }
    }
  }
}
```

---

### Method 7 — Clone & run from source (development / recommended for macOS local)

This is the most reliable method on macOS — no wrapper scripts, no `realpath` issues.

```bash
# 1. Install uv (skip if already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone repo
git clone https://github.com/openags/paper-search-mcp.git
cd paper-search-mcp

# 3. Verify it runs (uv auto-resolves dependencies, no manual install needed)
uv run -m paper_search_mcp.server
```

**Claude Desktop config** (replace the directory path with your actual clone location):

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/paper-search-mcp",
        "-m",
        "paper_search_mcp.server"
      ],
      "env": {
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "your@email.com",
        "PAPER_SEARCH_MCP_CORE_API_KEY": "",
        "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "",
        "PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN": "",
        "PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL": "",
        "PAPER_SEARCH_MCP_IEEE_API_KEY": "",
        "PAPER_SEARCH_MCP_ACM_API_KEY": ""
      }
    }
  }
}
```

For example, if you cloned to `/Users/mac/Pengsong/paper-search-mcp`:

```json
"args": ["run", "--directory", "/Users/mac/Pengsong/paper-search-mcp", "-m", "paper_search_mcp.server"]
```

> `uv run` automatically installs dependencies into an isolated environment on first run — no `pip install` or `venv` needed.

For active development, optionally install an editable copy:

```bash
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

---

### Environment Variables (`.env` file)

Instead of putting keys directly in the JSON config you can store them in a `.env` file in the project root (auto-loaded on startup):

```bash
cp .env.example .env   # if running from source
# or create ~/.paper-search-mcp.env for global use
```

```dotenv
PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=your@email.com
PAPER_SEARCH_MCP_CORE_API_KEY=
PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY=
PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN=
PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL=
PAPER_SEARCH_MCP_IEEE_API_KEY=
PAPER_SEARCH_MCP_ACM_API_KEY=
```

To use a custom path: `export PAPER_SEARCH_MCP_ENV_FILE=/absolute/path/to/.env`

> Legacy variable names without the `PAPER_SEARCH_MCP_` prefix (e.g. `CORE_API_KEY`, `UNPAYWALL_EMAIL`) are still supported for backward compatibility.

MinerU key setup helpers:

- `mineru_setup_status` reports whether `PAPER_SEARCH_MCP_MINERU_API_KEY` is configured.
- If the key is missing, expired, or rejected by the extract API, parse and health-check results may include `mineru_api_key_prompt`.
- MCP Apps-capable clients can render `render_mineru_api_key_setup_app`, backed by `ui://paper-search/mineru-api-key.html`. The widget saves the key by calling `configure_mineru_api_key`, which writes `PAPER_SEARCH_MCP_MINERU_API_KEY` into the active `.env` file.
- The MinerU key widget and paper-selection widget use the same restrained liquid-glass visual style. Whether a host displays them inline is controlled by the MCP host; non-Apps hosts can still use tool results and numbered fallback flows.

MinerU extract uploads use Aliyun OSS signed URLs. To avoid local/system proxy TLS interruptions during those uploads, the parser adds `.aliyuncs.com` and `mineru.oss-cn-shanghai.aliyuncs.com` to `NO_PROXY` / `no_proxy` in the current process by default. Existing entries are preserved.

```dotenv
# Disable the automatic OSS proxy bypass if your network requires OSS through a proxy.
PAPER_SEARCH_MCP_MINERU_OSS_NO_PROXY=false

# Override the default bypass hosts.
PAPER_SEARCH_MCP_MINERU_OSS_NO_PROXY_HOSTS=.aliyuncs.com,mineru.oss-cn-shanghai.aliyuncs.com
```

---

## Contributing

We welcome contributions! Here's how to get started:

1. **Fork the Repository**:
   Click "Fork" on GitHub.

2. **Clone and Set Up**:

   ```bash
   git clone https://github.com/yourusername/paper-search-mcp.git
   cd paper-search-mcp
   uv venv && source .venv/bin/activate
   uv pip install -e ".[dev]"
   ```

3. **Make Changes**:

   - Add new platforms in `academic_platforms/`.
   - Update tests in `tests/`.

4. **Submit a Pull Request**:
   Push changes and create a PR on GitHub.

---

## Demo

<img src="docs\images\demo.png" alt="Demo" width="800">

## TODO

### Planned Academic Platforms

- [√] arXiv
- [√] PubMed
- [√] bioRxiv
- [√] medRxiv
- [√] Google Scholar
- [√] IACR ePrint Archive
- [√] Semantic Scholar
- [√] Crossref
- [√] PubMed Central (PMC)
- [√] CORE
- [√] Europe PMC
- [√] Sci-Hub warning and enablement docs

### Development Tasks

- [√] Fix Async search bugs and ensure reliable fast MCP events
- [√] End-to-End full pipeline testing script (search, parse, download)
- [√] Establish two-layer federated architecture (Layer 1 tool: `search_papers`)
- [√] Ensure pervasive DOI extraction across metadata fields & abstract fallbacks
- [ ] Citation graph & Paper relation context feature
- [√] Expand full-stack OpenAlex provider

### Priority Free and Open Sources

- [√] PubMed Central (PMC)
- [√] CORE
- [√] OpenAlex
- [√] Europe PMC
- [√] OpenAIRE
- [√] dblp
- [√] CiteSeerX
- [√] DOAJ
- [√] BASE
- [√] Zenodo
- [√] HAL
- [√] SSRN (discovery + best-effort full-text)
- [√] Unpaywall (standalone DOI search source)

### Optional and Non-Core Integrations

- [ ] ResearchGate
- [ ] JSTOR
- [ ] ScienceDirect
- [ ] Springer Link
- [√] IEEE Xplore (optional skeleton — activate with `IEEE_API_KEY`)
- [√] ACM Digital Library (optional skeleton — activate with `ACM_API_KEY`)
- [ ] Web of Science
- [ ] Scopus

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.

---

Happy researching with `paper-search-mcp`! If you encounter issues, open a GitHub issue.

---

## Acknowledgements

This fork and extension benefited from the following open-source projects and prior art:

- [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp): the upstream MCP server for multi-source academic paper search and download.
- [Dictation354/paper-fetch-skill](https://github.com/Dictation354/paper-fetch-skill): reference workflow for paper fetching, PDF retrieval, and agent-facing paper utilities.
- [Rimagination/scansci-pdf](https://github.com/Rimagination/scansci-pdf): reference ideas for scientific PDF processing and extraction-oriented workflows.
- [yilewang/llm-for-zotero](https://github.com/yilewang/llm-for-zotero): reference implementation direction for integrating MinerU-style PDF parsing into a research-reading workflow.
- [opendatalab/MinerU](https://github.com/opendatalab/MinerU): the document parsing engine used for high-quality PDF-to-Markdown/JSON/assets extraction.
