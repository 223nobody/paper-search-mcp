<h2 align="center">Paper Search MCP</h1>

`paper-search-mcp` 是一个面向论文检索、PDF 下载和论文解析的 MCP Server，也提供 `paper-search` 命令行工具。当前版本在原始多源论文检索能力上，补齐了“检索/下载 PDF -> 询问用户是否解析 -> MinerU PDF parsing -> 缓存与同名 zip 导出”的完整链路。

<p align="center"><a href="README_CN.md">Chinese</a> | <a href="README.md">English</a></p>

## 项目定位

这个项目适合把论文检索和阅读前处理接入 LLM Agent 工作流：

- 从多个公开学术数据源检索论文，并统一输出论文条目。
- 优先使用开放获取和来源原生 PDF 链接下载论文。
- 当 MCP 工具实际保存 PDF 后，自动返回解析询问；支持 MCP Elicitation 多选 UI，也支持无 UI 环境下的编号选择。
- 使用 MinerU 将 PDF 解析为 Markdown、结构化 JSON 和图片/表格/公式等资源。
- 将解析产物缓存起来，方便后续按论文 key 读取、搜索和复用。

## 主要功能

- **多源论文检索**：支持 arXiv、PubMed、bioRxiv、medRxiv、Semantic Scholar、Crossref、OpenAlex、PMC、CORE、Europe PMC、dblp、OpenAIRE、CiteSeerX、DOAJ、BASE、Zenodo、HAL、SSRN、Unpaywall 等来源。
- **统一结果格式**：不同来源返回的论文会被整理为统一字段，便于 Agent 后续选择、下载和解析。
- **开放获取优先下载**：`download_with_fallback` 会优先使用来源原生下载、开放仓储、Unpaywall 等路径；Sci-Hub 保持可选且默认不启用。
- **保存 PDF 后自动询问解析**：只要 MCP 工具路径中发生 PDF 保存行为，就会返回 `parse_prompt`，提示用户是否进行 MinerU PDF parsing。
- **Checkbox/多选 UI 与降级机制**：支持 MCP Elicitation 的客户端可以显示原生多选表单；不支持时返回 `selection_token` 和编号列表，再用 `parse_selected_papers` 解析。
- **MinerU 优先解析**：支持官方 extract API、本地 MinerU API、MinerU CLI，并保留 `pypdf` 作为兜底文本提取方式。
- **同名 zip 导出**：解析后会在 PDF 所在文件夹生成同名 `.zip`，例如 `paper.pdf` -> `paper.zip`。
- **解析缓存**：解析结果会同步保存到 `.paper_search_cache`，包括 `full.md`、`content_list.json`、`manifest.json` 和 `assets/`。
- **CLI 与 MCP 双入口**：既可以作为 MCP Server 供 VS Code Copilot Agent Mode、Claude Desktop 等客户端调用，也可以直接用命令行运行。

## 安装与本地运行

推荐在当前源码目录运行：

```powershell
cd C:\code\paper-search-mcp
uv run -m paper_search_mcp.server
```

验证命令行入口：

```powershell
uv run paper-search sources
uv run paper-search search "multi objective reinforcement learning" -s arxiv,semantic,openalex -n 3
```

如果需要开发模式安装：

```powershell
cd C:\code\paper-search-mcp
uv venv
.\.venv\Scripts\activate
uv pip install -e ".[dev]"
```

## MCP 客户端配置示例

VS Code `.vscode/mcp.json` 示例：

```json
{
  "servers": {
    "paper-search-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\code\\paper-search-mcp",
        "-m",
        "paper_search_mcp.server"
      ],
      "env": {
        "PAPER_SEARCH_MCP_MINERU_MODE": "auto",
        "PAPER_SEARCH_MCP_MINERU_API_KEY": "",
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": ""
      }
    }
  }
}
```

Claude Desktop 的配置结构通常使用 `mcpServers`：

```json
{
  "mcpServers": {
    "paper-search-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\code\\paper-search-mcp",
        "-m",
        "paper_search_mcp.server"
      ]
    }
  }
}
```

## 环境变量

推荐在仓库根目录创建 `.env`，不要把真实 token 写入 README 或提交到 Git：

```dotenv
PAPER_SEARCH_MCP_CACHE_DIR=.paper_search_cache

PAPER_SEARCH_MCP_MINERU_MODE=auto
PAPER_SEARCH_MCP_MINERU_API_KEY=
PAPER_SEARCH_MCP_MINERU_EXTRACT_BASE_URL=https://mineru.net/api/v4
PAPER_SEARCH_MCP_MINERU_MODEL_VERSION=vlm
PAPER_SEARCH_MCP_MINERU_LANGUAGE=ch
PAPER_SEARCH_MCP_MINERU_IS_OCR=false
PAPER_SEARCH_MCP_MINERU_ENABLE_FORMULA=true
PAPER_SEARCH_MCP_MINERU_ENABLE_TABLE=true

PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=
PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY=
PAPER_SEARCH_MCP_CORE_API_KEY=
PAPER_SEARCH_MCP_DOAJ_API_KEY=
PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN=
PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL=
```

MinerU 解析模式说明：

- `auto`：如果配置了 `PAPER_SEARCH_MCP_MINERU_API_KEY`，优先调用 MinerU 官方 extract API；失败后继续尝试本地 API、CLI 和 `pypdf`。
- `extract`：只使用 MinerU 官方 extract API，适合你希望强制走在线解析服务的场景。
- `local_api`：只使用本地 MinerU API。
- `cli`：只使用本机 MinerU CLI。
- `pypdf`：只做基础文本提取，不会得到 MinerU 的版面结构和图片资产。

## 默认保存位置

当前默认 PDF 保存路径是：

```text
~/Desktop
```

在 Windows 上会解析为当前用户桌面，例如：

```text
C:\Users\<你的用户名>\Desktop
```

你也可以在 CLI 或 MCP 工具参数中传入 `save_path` 覆盖默认位置。

## MinerU 解析输出

对某个 PDF 执行 MinerU PDF parsing 后，会产生两类结果。

第一类是 PDF 所在目录下的同名 zip：

```text
~/Desktop/example.pdf
~/Desktop/example.zip
```

第二类是项目缓存目录：

```text
.paper_search_cache/
  papers/
    <paper_key>/
      source.pdf
      metadata.json
      status.json
      mineru/
        full.md
        content_list.json
        manifest.json
        assets/
```

常见文件含义：

- `full.md`：面向阅读和 LLM 上下文注入的完整 Markdown。
- `content_list.json`：MinerU 风格的结构化内容列表，适合后续按段落、标题、图片、表格等类型处理。
- `manifest.json`：解析器、模式、后端、原始 PDF、生成时间等元信息。
- `assets/`：解析得到的图片、表格、公式等资源文件。如果使用 `pypdf` 兜底，通常不会生成丰富图片资产。
- `<pdf 同名>.zip`：将 MinerU 结果导出的压缩包，保存在原 PDF 所在文件夹。

## 常用 CLI

检索论文：

```powershell
uv run paper-search search "agentic spatial reasoning" -s arxiv,semantic,openalex -n 3
```

下载论文，默认保存到桌面：

```powershell
uv run paper-search download arxiv 2401.12345
```

解析本地 PDF：

```powershell
uv run paper-search parse "$env:USERPROFILE\Desktop\example.pdf" --paper-key example --mode extract
```

查看 MinerU/解析环境：

```powershell
uv run paper-search mineru-health --mode auto
```

读取缓存结果：

```powershell
uv run paper-search cache list
uv run paper-search cache get example -f markdown
uv run paper-search cache get example -f json
uv run paper-search cache assets example
uv run paper-search cache search example "regularization"
```

## MCP 使用流程

### 1. 只检索论文

调用：

```json
{
  "tool": "search_papers",
  "arguments": {
    "query": "multi objective reinforcement learning",
    "sources": "arxiv,semantic,openalex",
    "max_results_per_source": 3
  }
}
```

适合只想拿到论文条目、标题、作者、DOI、PDF 链接等元数据的场景。

### 2. 检索后弹出多选解析

调用：

```json
{
  "tool": "search_papers_with_elicitation",
  "arguments": {
    "query": "agentic spatial reasoning",
    "sources": "arxiv,semantic,openalex",
    "max_results_per_source": 3,
    "save_path": "~/Desktop",
    "mode": "extract"
  }
}
```

如果 MCP 客户端支持 Elicitation，例如 VS Code Copilot Agent Mode，并且客户端把 `array + enum` 渲染为多选控件，就可以看到 checkbox 或多选列表。用户选择论文后，MCP Server 会下载并解析所选条目。

### 3. 无 checkbox UI 时的编号选择

先生成后端选择 session：

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

返回结果中会包含：

- `selection_token`
- 编号后的 `papers`
- 每条论文的 `parse_ready` 状态

然后解析用户选择的编号：

```json
{
  "tool": "parse_selected_papers",
  "arguments": {
    "selection_token": "search_20260610_xxxxxxxx",
    "selected_indices": "1,3",
    "save_path": "~/Desktop",
    "mode": "extract"
  }
}
```

`selected_indices` 支持：

- `"all"`：解析全部可解析论文。
- `"1,3,5"`：解析指定编号。
- `"2-4"`：解析一个连续范围。

### 4. 保存 PDF 后自动询问解析

以下 MCP 工具路径只要实际保存了 PDF，就会返回 `parse_prompt` 或 `saved_pdf_prompt`：

- 各平台 `download_*` 工具。
- 各平台 `read_*` 工具中发生 PDF 下载的情况。
- `download_with_fallback`。

如果客户端支持 Elicitation，会尝试弹出多选 UI；如果不支持，会返回编号列表和 `selection_token`，再由 Agent 调用 `parse_selected_papers`。

### 5. 直接解析本地 PDF

如果你已经有本地 PDF，可以直接调用：

```json
{
  "tool": "parse_pdf_with_mineru",
  "arguments": {
    "pdf_path": "C:\\Users\\<你的用户名>\\Desktop\\example.pdf",
    "paper_key": "example",
    "mode": "extract"
  }
}
```

解析完成后，会在 `example.pdf` 同目录生成 `example.zip`，并在 `.paper_search_cache` 中保留可复用缓存。

## 常用 MCP 工具

- `search_papers`：多源检索并去重。
- `search_papers_with_elicitation`：检索后通过 Elicitation 请求用户多选论文并解析。
- `search_papers_for_parsing`：检索并创建编号选择 session。
- `parse_selected_papers`：根据 `selection_token` 和编号下载/解析论文。
- `list_search_sessions` / `get_search_session` / `delete_search_session`：管理编号选择 session。
- `download_with_fallback`：按开放获取优先策略下载 PDF。
- `parse_downloaded_paper`：下载论文后直接进入 MinerU 解析流程。
- `parse_pdf_with_mineru`：解析本地 PDF。
- `mineru_health_check`：检查 MinerU extract/API/CLI/pypdf 可用性。
- `list_parsed_papers` / `get_parsed_paper` / `search_parsed_paper` / `get_paper_assets`：读取和检索解析缓存。

## 典型使用场景

- **快速找论文**：调用 `search_papers`，拿到多源聚合结果。
- **找论文并立刻解析**：调用 `search_papers_with_elicitation`，在支持的客户端中勾选要解析的论文。
- **客户端没有 UI**：使用 `search_papers_for_parsing` + `parse_selected_papers`。
- **已有 PDF**：调用 `parse_pdf_with_mineru` 或 CLI `paper-search parse`。
- **构建论文知识库**：批量解析 PDF 后，通过 `cache get/search/assets` 读取 Markdown、JSON 和图片资产。
- **排查解析质量**：优先检查同名 zip 和 `.paper_search_cache/papers/<paper_key>/mineru/manifest.json`，确认使用的是 `extract`、`local_api`、`cli` 还是 `pypdf`。

## 合规与安全提示

- 项目默认采用开放获取优先策略，建议优先使用来源原生 PDF、开放仓储和 Unpaywall 等路径。
- Sci-Hub 属于可选能力，不应作为默认下载路径；是否启用以及如何使用由用户自行承担责任。
- 不要把 `PAPER_SEARCH_MCP_MINERU_API_KEY`、Semantic Scholar key、CORE key 等真实凭据写入 README、提交记录或公开 issue。
- `.env` 适合保存本地凭据，发布前应确认 `.gitignore` 已忽略该文件。

## 测试

常用回归测试命令：

```powershell
python -m unittest tests.test_selection_sessions tests.test_mineru_parser tests.test_fallback tests.test_config_env tests.test_server
```

文档修改本身不需要运行完整测试；当修改解析、下载、Elicitation 或缓存逻辑时，建议至少运行以上测试。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。

## 致谢

本项目的 fork、功能补齐和设计参考了以下 GitHub 仓库：

- [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp)：原始上游项目，提供多源论文检索、下载和 MCP Server 基础能力。
- [Dictation354/paper-fetch-skill](https://github.com/Dictation354/paper-fetch-skill)：提供论文抓取、下载和 Agent 使用流程方面的参考。
- [Rimagination/scansci-pdf](https://github.com/Rimagination/scansci-pdf)：提供科学 PDF 处理和解析工作流方面的参考。
- [yilewang/llm-for-zotero](https://github.com/yilewang/llm-for-zotero)：提供将 MinerU PDF parsing 接入论文阅读/管理流程的实现思路参考。
- [opendatalab/MinerU](https://github.com/opendatalab/MinerU)：提供高质量 PDF 解析、Markdown/JSON/资源抽取能力，是本项目 MinerU 解析链路的核心依赖方向。
