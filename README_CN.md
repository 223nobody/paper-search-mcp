<h1 align="center">Paper Search MCP</h1>

`paper-search-mcp` 是一个面向论文检索、PDF 下载和论文解析的 MCP Server，也提供 `paper-search` 命令行工具。当前版本在原始多源论文检索能力上，补齐了“检索/下载 PDF -> 解析提示或后台 MinerU 解析任务 -> PDF 同目录解析产物与轻量缓存”的完整链路。

<p align="center"><a href="README_CN.md">Chinese</a> | <a href="README.md">English</a></p>

## 项目定位

这个项目适合把论文检索和阅读前处理接入 LLM Agent 工作流：

- 从多个公开学术数据源检索论文，并统一输出论文条目。
- 优先使用开放获取和来源原生 PDF 链接下载论文。
- 当 MCP 工具实际保存 PDF 后，会返回解析提示；单篇下载可自动解析，小批量/大批量选择下载可走后台解析任务或 checkbox/编号选择。
- 使用 MinerU 将 PDF 解析为 Markdown、结构化 JSON 和图片/表格/公式等资源。
- 将解析产物缓存起来，方便后续按论文 key 读取、搜索和复用。

## 主要功能

- **多源论文检索**：支持 arXiv、PubMed、bioRxiv、medRxiv、Semantic Scholar、Crossref、OpenAlex、PMC、CORE、Europe PMC、dblp、OpenAIRE、CiteSeerX、DOAJ、BASE、Zenodo、HAL、SSRN、Unpaywall 等来源。
- **统一结果格式**：不同来源返回的论文会被整理为统一字段，便于 Agent 后续选择、下载和解析。
- **开放获取优先下载**：`download_with_fallback` 会优先使用来源原生下载、开放仓储、Unpaywall 等路径；Sci-Hub 保持可选且默认不启用。
- **保存 PDF 后解析提示 / 大批量选择**：只要 MCP 工具路径中发生 PDF 保存行为，就会返回 `parse_prompt`；批量下载不会同步阻塞解析，而是建议 `submit_parse_job` 或 checkbox/编号选择。
- **Checkbox/多选 UI 与降级机制**：支持 MCP Elicitation 或 MCP Apps 的客户端可以显示多选/checkbox UI；不支持时返回 `selection_token` 和编号列表，再用 `submit_parse_job` 或 `parse_selected_papers` 解析。
- **MinerU 优先解析**：支持官方 extract API、本地 MinerU API、MinerU CLI，并保留 `pypdf` 作为兜底文本提取方式。
- **MinerU 批量 extract**：在 `mode="extract"` / `mode="cloud_api"` 下，多篇 PDF 会合并提交到 MinerU `/file-urls/batch`，减少逐篇请求、轮询和下载开销；`auto` 模式可用环境变量显式开启。
- **PDF 同目录产物**：解析后会在 PDF 所在文件夹生成 `<pdf 文件名>_mineru/`，其中包含 `full.md`、`content_list.json`、`manifest.json` 和 `assets/`。
- **可选同名 zip 导出**：默认不生成同名 `.zip`；如需打包副本，可设置 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true`，例如 `paper.pdf` -> `paper.zip`。
- **FTS 解析索引**：`.paper_search_cache` 会维护轻量 SQLite FTS 索引，用于快速搜索已解析内容；不可用时自动退回文件搜索。
- **后台解析任务**：长时间批量解析可用 `submit_parse_job` 提交，再用 `get_parse_job_status`、`list_parse_jobs`、`cancel_parse_job` 管理。
- **轻量解析缓存**：`.paper_search_cache` 只保存 metadata、status、session、下载健康统计和轻量 manifest/index，不再复制原 PDF，也不再保存一份完整解析内容。
- **MCP 优先、CLI 兜底**：自然语言 Agent 场景优先通过 MCP 工具调用；命令行工具保留给手动验证、脚本和 MCP 不可用时的兜底。

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
PAPER_SEARCH_MCP_SEARCH_PROFILE=fast
PAPER_SEARCH_MCP_SEARCH_TIMEOUT_SECONDS=18
PAPER_SEARCH_MCP_SEARCH_SOURCE_TIMEOUT_SECONDS=12
PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS=300
PAPER_SEARCH_MCP_PARSE_CONCURRENCY=3

PAPER_SEARCH_MCP_MINERU_MODE=auto
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

PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=
PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY=
PAPER_SEARCH_MCP_CORE_API_KEY=
PAPER_SEARCH_MCP_DOAJ_API_KEY=
PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN=
PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL=
```

### MinerU API Key 配置弹窗

支持 MCP Apps 的客户端可以调用 `mineru_setup_status` 检查配置状态。若未配置 `PAPER_SEARCH_MCP_MINERU_API_KEY`，返回结果会包含 `mineru_api_key_prompt`，指向 `render_mineru_api_key_setup_app` 和 `ui://paper-search/mineru-api-key.html`，客户端可据此显示输入框弹窗。

也可以直接调用：

```json
{
  "tool": "render_mineru_api_key_setup_app",
  "arguments": {
    "reason": "missing"
  }
}
```

Widget 中提交后会调用 `configure_mineru_api_key`，把 key 写入项目 `.env`：

```text
C:\code\paper-search-mcp\.env
```

保存的变量名是：

```dotenv
PAPER_SEARCH_MCP_MINERU_API_KEY=<你的 MinerU API key>
```

如果解析时检测到 MinerU API key 缺失、过期、401/403、token invalid/expired 等鉴权错误，`parse_pdf_with_mineru`、`parse_downloaded_paper` 和 `parse_selected_papers` 的返回结果会附带 `mineru_api_key_prompt`，用于提示用户重新配置。

说明：MCP Server 可以提供 Apps UI 和提示数据，但“安装 MCP 后是否自动弹窗”由具体 MCP Host 决定。支持启动/安装后自动调用工具的 Host 应调用 `mineru_setup_status`；不支持的 Host 仍可由 Agent 在首次解析或健康检查时打开配置 UI。

MinerU key 配置弹窗和论文 checkbox 选择器使用统一的简约液态玻璃风格：半透明面板、柔和边框、暗色模式适配和紧凑布局。是否能在对话中内嵌显示仍取决于 MCP Host 是否支持 MCP Apps。

MinerU 解析模式说明：

- `auto`：如果配置了 `PAPER_SEARCH_MCP_MINERU_API_KEY`，优先调用 MinerU 官方 extract API；失败后继续尝试本地 API、CLI 和 `pypdf`。
- `extract`：只使用 MinerU 官方 extract API，适合你希望强制走在线解析服务的场景。
- `local_api`：只使用本地 MinerU API。
- `cli`：只使用本机 MinerU CLI。
- `pypdf`：只做基础文本提取，不会得到 MinerU 的版面结构和图片资产。

MinerU 官方 extract 上传阶段会使用阿里云 OSS 签名 URL。为避免本机系统代理或本地代理导致 OSS 上传 TLS 握手中断，解析器默认会在当前进程中把 `.aliyuncs.com` 和 `mineru.oss-cn-shanghai.aliyuncs.com` 合并写入 `NO_PROXY` / `no_proxy`，已有条目会保留。

```dotenv
# 如果你的网络必须通过代理访问 OSS，可以关闭默认绕过。
PAPER_SEARCH_MCP_MINERU_OSS_NO_PROXY=false

# 如 MinerU 后续返回了其他 OSS 域名，可自定义绕过列表。
PAPER_SEARCH_MCP_MINERU_OSS_NO_PROXY_HOSTS=.aliyuncs.com,mineru.oss-cn-shanghai.aliyuncs.com
```

检索默认使用 `fast` profile，不再默认等待所有慢源。需要更全覆盖时，在工具参数中传 `sources="deep"` 或 `sources="all"`。`PAPER_SEARCH_MCP_SEARCH_TIMEOUT_SECONDS` 控制整体检索超时，`PAPER_SEARCH_MCP_SEARCH_SOURCE_TIMEOUT_SECONDS` 控制单来源超时，`PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS` 控制短期查询缓存。

批量解析时可用 `PAPER_SEARCH_MCP_PARSE_CONCURRENCY` 控制并发度。`mode="extract"` / `mode="cloud_api"` 会优先走 MinerU 官方多文件 batch；如果希望 `auto` 模式也启用真批处理，可设 `PAPER_SEARCH_MCP_MINERU_BATCH_PARSE=true`。上传和结果 zip 下载并发度可分别用 `PAPER_SEARCH_MCP_MINERU_UPLOAD_CONCURRENCY`、`PAPER_SEARCH_MCP_MINERU_DOWNLOAD_CONCURRENCY` 调整。`PAPER_SEARCH_MCP_MINERU_AUTO_ORDER` 控制 auto 模式顺序，例如本地 MinerU 服务常驻时可设为 `local_api,extract,cli,pypdf`。默认不生成同名 zip；如需额外打包副本，可设 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true`。

## 默认保存位置

当前默认 PDF 保存路径是：

```text
~/Desktop/papers
```

在 Windows 上会解析为当前用户桌面，例如：

```text
C:\Users\<你的用户名>\Desktop\papers
```

你也可以在 CLI 或 MCP 工具参数中传入 `save_path` 覆盖默认位置。

## MinerU 解析输出

对某个 PDF 执行 MinerU PDF parsing 后，默认会产生两类结果。

第一类是 PDF 所在目录下的解析产物目录：

```text
~/Desktop/example.pdf
~/Desktop/example_mineru/
  full.md
  content_list.json
  manifest.json
  metadata.json
  status.json
  assets/
```

如果设置 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true`，还会额外生成 PDF 所在目录下的同名 zip：

```text
~/Desktop/example.zip
```

第二类是项目缓存目录中的轻量索引：

```text
.paper_search_cache/
  papers/
    <paper_key>/
      metadata.json
      status.json
      mineru/
        manifest.json
```

`mineru/manifest.json` 只保留必要元信息和 PDF 同目录产物路径，避免在 MCP 缓存目录中重复存放 PDF、Markdown、content list 和 assets。

常见文件含义：

- `full.md`：面向阅读和 LLM 上下文注入的完整 Markdown。
- `content_list.json`：MinerU 风格的结构化内容列表，适合后续按段落、标题、图片、表格等类型处理。
- `manifest.json`：解析器、模式、后端、原始 PDF、生成时间等元信息。
- `assets/`：解析得到的图片、表格、公式等资源文件。如果使用 `pypdf` 兜底，通常不会生成丰富图片资产。
- `<pdf 同名>.zip`：可选打包副本；仅当 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true` 时生成，保存在原 PDF 所在文件夹。

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
uv run paper-search cache search-index "regularization" --paper-key example
uv run paper-search cache rebuild-index
uv run paper-search cache download-health
uv run paper-search parse-batch C:\Users\you\Desktop\a.pdf C:\Users\you\Desktop\b.pdf --mode extract
```

本地优化效果可以用 benchmark 脚本验证，不依赖网络：

```powershell
uv run python scripts\bench_search_parse.py --pdf-count 8 --mode pypdf --force
```

输出会包含首次解析、缓存命中解析、FTS 重建/搜索、传统文件搜索耗时和 speedup。

## MCP 使用流程

### 1. 自然语言首选高层入口

对 VS Code Copilot Agent Mode、Claude Desktop、Claude Code 等 MCP Host，
推荐优先调用 `paper_research_workflow`。它会把检索、排序、下载和解析任务提交都留在 MCP 内完成，
避免 Agent 为了论文任务打开终端、激活 `.venv` 或执行 CLI 命令。

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

常用参数：

- `intent="search_only"`：只检索并返回候选论文/选择 session。
- `intent="search_download"`：检索并下载排名靠前的论文。
- `intent="search_download_parse"`：检索、下载，并通过 `submit_parse_job` 提交后台解析任务。
- `selection_mode="manual"`：先返回 checkbox/编号选择，不自动下载。
- `parse_execution="background"`：默认后台解析，返回 `parse_job.job_id`，再用 `get_parse_job_status` 查询。
- `parse_execution="sync"`：仅在明确需要当前调用等待解析完成时使用。

除非用户明确指定目录，不要传 `save_path`；默认保存到 `~/Desktop/papers`。

### 2. 只检索论文

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

### 3. 检索后弹出多选解析

调用：

```json
{
  "tool": "search_papers_with_elicitation",
  "arguments": {
    "query": "agentic spatial reasoning",
    "sources": "arxiv,semantic,openalex",
    "max_results_per_source": 3,
    "save_path": "~/Desktop/papers",
    "mode": "extract"
  }
}
```

如果 MCP 客户端支持 Elicitation，例如 VS Code Copilot Agent Mode，并且客户端把 `array + enum` 渲染为多选控件，就可以看到 checkbox 或多选列表。用户选择论文后，MCP Server 会下载并解析所选条目。

### 3. MCP Apps checkbox UI

支持 MCP Apps 的客户端可以打开独立 checkbox 组件，适合检索候选论文后手动选择，或单次保存超过 10 篇 PDF 后选择要解析的论文：

```json
{
  "tool": "render_paper_selection_app",
  "arguments": {
    "selection_token": "search_20260610_xxxxxxxx",
    "mode": "extract"
  }
}
```

该工具会返回 `ui://paper-search/paper-selection.html` 对应的 HTML widget。Widget 中勾选论文后，会通过 Apps bridge 调用 `submit_parse_job` 提交后台解析任务。若客户端不支持 MCP Apps，仍可使用下面的编号选择流程。

如果当前 Host 不能渲染 MCP Apps，但允许打开系统浏览器，可以调用本地浏览器选择页：

```json
{
  "tool": "open_paper_selection_page",
  "arguments": {
    "selection_token": "search_20260610_xxxxxxxx",
    "mode": "extract",
    "open_browser": true
  }
}
```

该工具会启动一个 localhost 页面并使用系统默认浏览器打开；页面提交后同样会调用 `submit_parse_job`。MCP Server 不能强制打开 Codex 或其他 Host 的内置浏览器，只能返回 URL 或请求系统浏览器打开。

### 4. 无 checkbox UI 时的编号选择

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

### 5. 保存 PDF 后自动解析 / 大批量选择

以下 MCP 工具路径只要实际保存了 PDF，就会返回 `parse_prompt` 或 `saved_pdf_prompt`：

- 各平台 `download_*` 工具。
- 各平台 `read_*` 工具中发生 PDF 下载的情况。
- `download_with_fallback`。

保存后的处理规则：

- 单篇下载/读取工具保存 **10 篇及以下** PDF 时，可自动解析全部可解析 PDF，`parse_prompt.interaction` 为 `auto_parse_saved_pdfs`。
- `download_selected_papers` 面向批量下载：只下载并写 manifest，同时返回 `parse_prompt`。10 篇及以下会建议使用 `submit_parse_job` 后台解析；超过 10 篇会返回 checkbox/编号选择提示。支持 MCP Apps 的客户端可使用 `render_paper_selection_app` 渲染 checkbox；都不支持时返回编号列表和 `selection_token`。

### 6. 直接解析本地 PDF

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

解析完成后，默认会在 `example.pdf` 同目录生成 `example_mineru/`；如果显式开启 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true`，才会额外生成 `example.zip`。`.paper_search_cache` 只保留按 `paper_key` 查找这些产物所需的轻量索引。

## 常用 MCP 工具

- `search_papers`：多源检索并去重。
- `paper_research_workflow`：自然语言 Agent 首选高层入口，可一次完成检索、下载，并按需提交后台解析任务。
- `search_papers_with_elicitation`：检索后通过 Elicitation 请求用户多选论文并解析。
- `search_papers_for_parsing`：检索并创建编号选择 session。
- `render_paper_selection_app`：为支持 MCP Apps 的客户端渲染 checkbox 论文选择器。
- `open_paper_selection_page`：为不支持 MCP Apps 的客户端打开本地浏览器 checkbox 选择页。
- `download_selected_papers`：根据 selection session 批量下载论文，写 manifest，并返回解析提示。
- `crawl_download_parse_papers`：兼容旧流程的下载入口；新 Agent 工作流优先使用 `paper_research_workflow`。
- `parse_selected_papers`：根据 `selection_token` 和编号下载/解析论文。
- `submit_parse_job` / `get_parse_job_status`：提交和查询后台解析任务。
- `mineru_setup_status`：检查 MinerU API key 配置状态，未配置时返回 MCP Apps 配置弹窗入口。
- `render_mineru_api_key_setup_app`：渲染 MinerU API key 输入框。
- `configure_mineru_api_key`：把 MinerU API key 写入 `.env`。
- `list_search_sessions` / `get_search_session` / `delete_search_session`：管理编号选择 session。
- `download_with_fallback`：按开放获取优先策略下载 PDF。
- `parse_downloaded_paper`：下载论文后直接进入 MinerU 解析流程。
- `parse_pdf_with_mineru`：解析本地 PDF。
- `mineru_health_check`：检查 MinerU extract/API/CLI/pypdf 可用性。
- `list_parsed_papers` / `get_parsed_paper` / `search_parsed_paper` / `get_paper_assets`：按 `paper_key` 读取和检索 PDF 同目录解析产物。

## 典型使用场景

- **快速找论文**：优先调用 `paper_research_workflow(intent="search_only")`，只需要低层结果时再直接调用 `search_papers`。
- **找论文、下载并解析**：调用 `paper_research_workflow(intent="search_download_parse", parse_execution="background")`，返回 `parse_job.job_id` 后用 `get_parse_job_status` 查询。
- **找论文并手动选择解析**：调用 `paper_research_workflow(intent="search_only", selection_mode="manual")`，在支持的客户端中使用 checkbox，或使用编号 fallback。
- **客户端支持 MCP Apps**：使用 `search_papers_for_parsing` + `render_paper_selection_app` 打开 checkbox 组件。
- **客户端不支持 MCP Apps 但可打开浏览器**：使用 `open_paper_selection_page` 打开本地 localhost checkbox 页面。
- **客户端没有 UI**：使用 `search_papers_for_parsing` + `submit_parse_job`；只有明确需要同步等待时才用 `parse_selected_papers`。
- **保存 PDF 后解析**：调用单篇 `download_*` 或 `download_with_fallback` 时，小批量可自动解析；调用 `download_selected_papers` 批量下载时，会返回 `parse_prompt`，再用 `submit_parse_job`、`render_paper_selection_app` 或 `parse_selected_papers` 进入 MinerU 解析。
- **已有 PDF**：调用 `parse_pdf_with_mineru` 或 `parse_pdfs_with_mineru`，CLI 仅作为 MCP 不可用时的兜底。
- **构建论文知识库**：批量解析 PDF 后，通过 `cache get/search/assets` 读取 Markdown、JSON 和图片资产。
- **排查解析质量**：优先检查 `<pdf 文件名>_mineru/manifest.json` 和同名 zip，确认使用的是 `extract`、`local_api`、`cli` 还是 `pypdf`。

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
