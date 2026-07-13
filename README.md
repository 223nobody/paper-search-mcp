<h1 align="center"> Paper Search MCP</h1>

<p align="center"><b>🔬 一站式学术论文 MCP 服务 —— 检索、下载、解析，让 AI Agent 成为你的研究助手</b></p>

`paper-search-mcp` 是一个面向论文检索、PDF 下载和论文解析的 MCP Server，也提供 `paper-search` 命令行工具。当前版本在原始多源论文检索能力上，补齐了“检索/下载 PDF -> 解析提示或后台 MinerU 解析任务 -> PDF 同目录解析产物与轻量缓存”的完整链路。

<p align="center">🌐 <a href="README.md">Chinese</a> | <a href="README_EN.md">English</a></p>

<p align="center">
  <img src="docs/images/workflow.png" alt="Paper Search MCP 工作流总览">
</p>

---

## 🤖 一键 AI 安装

将以下提示词粘贴到 Claude Code 或 Codex 中，AI 将自动完成安装：

```text
请帮我安装 paper-search-mcp。这是一个多源学术论文检索/下载/解析 MCP 服务。按以下流程操作：

## 1. 克隆仓库
git clone https://github.com/223nobody/paper-search-mcp.git ~/code/paper-search-mcp
（根据平台调整路径：macOS/Linux 用 ~/code/paper-search-mcp，Windows 用 C:\code\paper-search-mcp）

## 2. 检测平台并安装依赖
- 检查是否安装了 uv（Python 包管理器），如未安装则先安装。
- 进入项目目录，运行 `uv sync` 安装核心依赖。
- 运行 `uv sync --extra publisher` 安装 scansci-pdf。
  如果 scansci-pdf 在首次使用时自动安装失败，可手动执行：
  `uv pip install scansci-pdf[cloakbrowser]`
  `playwright install chromium`

## 3. 配置 .env
- 如果 .env 不存在，从 .env.example 复制一份。
- 引导用户填写必要的 API Key：
  - MinerU API Key（免费注册入口 https://mineru.net）
  - Semantic Scholar API Key（提升搜索速率限制，出版商 DOI 查找也依赖此 Key）
  - CORE API Key（可选——下载回退）
  - Elsevier API Key（可选——爬取 Elsevier/ScienceDirect 出版物，提升出版商下载成功率）
- 其余保持默认即可。默认的 pdf-cs 搜索 profile 只启用 arxiv/crossref/openalex/dblp 四个源（CS 领域优先）。
  如需启用更多数据源（如 semantic、pubmed），修改 PAPER_SEARCH_MCP_DISABLED_SOURCES 去掉对应源名称。

## 4. 注册为全局 MCP Server
运行安装脚本 `python scripts/install-mcp-global.py`。
这会将 MCP Server 配置写入 `~/.claude/mcp.json`，之后从任意工作区打开 Claude Code 都能使用，无需手动编辑 JSON。
（可选参数：`--dry-run` 预览变更、`--force` 强制覆盖、`--uninstall` 卸载）

## 5. 安装 Skill
- Claude Code：将 .claude/skills/paper-search/SKILL.md 复制到 ~/.claude/skills/paper-search/SKILL.md
- Codex：将 .codex/skills/paper-search/SKILL.md 复制到 ~/.codex/skills/paper-search/SKILL.md

## 6. 验证安装
- 运行 `uv run -m paper_search_mcp.server` 确认服务启动成功。
- 看到工具注册列表后，告知安装完成，列出核心功能，并提供几个示例 prompt。
- 建议首次使用时调用 `diagnose_paper_sources` 和 `mineru_health_check` 确认环境配置状态。
```

> 💡 **原理**：以上 prompt 就是给 AI Agent 的"安装脚本"。你不需要手动执行任何步骤——只需粘贴到 Claude Code，AI 会自动检测平台、克隆代码、配置环境、注册 MCP、验证安装。

---

## 📋 快速开始 & 核心文档

MCP 可用后，只需用自然语言描述需求即可，Agent 会自动调用对应工具。

> 📖 **[MCP 使用示例提示词.md](docs/MCP使用示例提示词.md)**  
> **11 个场景**的精确提示词模板 —— 一站式全流程、只检索、出版商下载、IEEE/ACM 搜索、解析本地 PDF、环境诊断等。附带参数速查表和搜索源配置说明。

> 🗂️ **[数据源配置指南.md](docs/数据源配置指南.md)**  
> 21 个数据源的完整说明 —— 能力矩阵、可靠性评分、搜索配置文件（fast/pdf-cs/agent-skill-fast/deep）、API Key 配置汇总、5 种推荐配置方案、下载 Fallback 链详解、环境变量完整参考。

**🔴 使用前务必阅读以上两份文档，让 AI Agent 最大程度发挥本 MCP 的全部能力。**

---

**推荐和 <a href="https://github.com/223nobody/Zotero-Obsidian-note" style="color:#31B0F2;text-decoration:underline;text-decoration-color:#31B0F2;">223nobody/paper-search-mcp</a> 一起使用**

## 🎯 项目定位

这个项目适合把论文检索和阅读前处理接入 LLM Agent 工作流：

- 🔍 从多个公开学术数据源检索论文，并统一输出论文条目。
- 📥 优先使用开放获取和来源原生 PDF 链接下载论文。
- 🤖 当 MCP 工具保存 PDF 后返回解析提示；单篇自动解析，大批量走 checkbox/编号选择。
- 🧠 使用 MinerU 将 PDF 解析为 Markdown、结构化 JSON 和图片/表格/公式等资源。
- 💾 将解析产物缓存起来，方便后续按论文 key 读取、搜索和复用。

## ✨ 主要功能

- 🔍 **多源论文检索**：支持 arXiv、PubMed、bioRxiv、medRxiv、Semantic Scholar、Crossref、OpenAlex、PMC、CORE、Europe PMC、dblp、OpenAIRE、CiteSeerX、DOAJ、BASE、Zenodo、HAL、SSRN、Unpaywall 等来源。
- 📋 **统一结果格式**：不同来源返回的论文会被整理为统一字段，便于 Agent 后续选择、下载和解析。
- 📖 **开放获取优先下载**：`download_with_fallback` 会优先使用来源原生下载、开放仓储、Unpaywall 等路径；Sci-Hub 保持可选且默认不启用。
- ⚡ **保存 PDF 后解析 / 大批量选择**：只要 MCP 工具路径中发生 PDF 保存行为，就会返回 `parse_prompt`；10 篇及以下默认自动提交 MinerU 后台解析任务，超过 10 篇先返回 checkbox/编号选择，用户确认后才下载并按需解析。
- ✅ **Checkbox/多选 UI 与降级机制**：支持 MCP Elicitation 或 MCP Apps 的客户端可以显示多选/checkbox UI；不支持时返回 `selection_token` 和编号列表，再按语义调用 `download_and_parse_selected_papers`、`submit_parse_job` 或 `parse_selected_papers`。
- 🧪 **MinerU 优先解析**：支持官方 extract API、本地 MinerU API、MinerU CLI，并保留 `pypdf` 作为兜底文本提取方式。
- 📦 **MinerU 批量 extract**：在 `mode="extract"` / `mode="cloud_api"` 下，多篇 PDF 会合并提交到 MinerU `/file-urls/batch`，减少逐篇请求、轮询和下载开销；`auto` 模式可用环境变量显式开启。
- 📂 **PDF 同目录产物**：解析后会在 PDF 所在文件夹生成 `<pdf 文件名>_mineru/`，其中包含 `full.md`、`content_list.json`、`manifest.json` 和 `assets/`。
- 🗜️ **可选同名 zip 导出**：默认不生成同名 `.zip`；如需打包副本，可设置 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true`，例如 `paper.pdf` -> `paper.zip`。
- 🔎 **FTS 解析索引**：`.paper_search_cache` 会维护轻量 SQLite FTS 索引，用于快速搜索已解析内容；不可用时自动退回文件搜索。
- 🕐 **后台解析任务**：长时间批量解析可用 `submit_parse_job` 提交，再用 `get_parse_job_status`、`list_parse_jobs`、`cancel_parse_job` 管理。
- 🪶 **轻量解析缓存**：`.paper_search_cache` 只保存 metadata、status、session、下载健康统计和轻量 manifest/index，不再复制原 PDF，也不再保存一份完整解析内容。
- 🔌 **MCP 优先、CLI 兜底**：自然语言 Agent 场景优先通过 MCP 工具调用；命令行工具保留给手动验证、脚本和 MCP 不可用时的兜底。
- 🏢 **出版商发行版下载（MCP Chaining）**：通过内置的 scansci-pdf MCP Chaining 集成，将已下载的 arXiv 论文自动升级为出版商最终发行版 PDF（Nature、Elsevier、Springer 等）。自动安装、零配置、按需使用，所有现有功能不受影响。

---

## 🧭 项目图示

### 系统架构

<p align="center">
  <img src="docs/images/system.png" alt="Paper Search MCP 系统架构图">
</p>

### 多源论文检索网络

<p align="center">
  <img src="docs/images/network_graph.png" alt="Paper Search MCP 多源论文检索网络图">
</p>

---

## 🎬 演示

### MCP Apps 论文选择器（仅 Codex Desktop 支持）

> ⚠️ **当前状态**：内置 MCP Apps（checkbox 论文选择器、MinerU API Key 配置弹窗等）目前**仅 Codex Desktop** 可以完整渲染。其他 MCP Host（VS Code、Claude Desktop、Claude Code 等）暂不支持 MCP Apps 内嵌渲染，会自动降级为本地浏览器 fallback 或编号列表选择。

|            Codex Desktop — MCP Apps 原生渲染             |                Codex 插件面板                 |
| :------------------------------------------------------: | :-------------------------------------------: |
| ![Codex Desktop MCP Apps](docs/images/codex_desktop.png) | ![Codex Plugin](docs/images/codex_plugin.png) |

**各平台 MCP Apps 支持情况：**

| 平台                       | MCP Apps 渲染 | 降级方案                       |
| -------------------------- | :-----------: | ------------------------------ |
| **Codex Desktop**          |  ✅ 完整支持  | —                              |
| VS Code（Codex / Copilot） |   ❌ 不支持   | 本地浏览器 checkbox 页面       |
| Claude Desktop             |   ❌ 不支持   | 编号列表 fallback              |
| Claude Code（CLI）         |   ❌ 不支持   | 编号列表 fallback / TUI 选择器 |
| Cursor                     |   ❌ 不支持   | 编号列表 fallback              |

> 💡 当 `render_paper_selection_app` 返回但未显示 checkbox UI 时，Agent 会自动调用 `open_paper_selection_page` 在系统浏览器中打开选择页面。

---

## 📦 安装与本地运行

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

---

## 🔧 环境变量

在仓库根目录创建 `.env`（不要将 token 提交到 Git）：

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

---

## 📊 数据源配置

`PAPER_SEARCH_MCP_DISABLED_SOURCES` 控制禁用哪些数据源。经 `diagnose_paper_sources` 实测，各数据源下载能力如下：

### ✅ 可直接下载 PDF 的数据源

| 数据源    | 适用领域          | 下载可靠性                         |
| --------- | ----------------- | ---------------------------------- |
| **arxiv** | 计算机科学 / 通用 | ✅ **唯一对 CS 可靠的 PDF 下载源** |
| pmc       | 生物医学 OA       | ✅ 仅生物医学                      |
| biorxiv   | 生物学预印本      | ✅ 仅生物学                        |
| medrxiv   | 医学预印本        | ✅ 仅医学                          |
| iacr      | 密码学预印本      | ✅ 仅密码学                        |

### 📋 仅元数据，无法下载 PDF

| 数据源         | 说明                         |
| -------------- | ---------------------------- |
| crossref       | DOI / 元数据主干，不托管 PDF |
| openalex       | 元数据和 OA 链接，不托管 PDF |
| dblp           | CS 文献元数据，不提供下载    |
| google_scholar | 发现引擎，不直接提供 PDF     |
| unpaywall      | OA URL 解析器，不托管 PDF    |

### ⚠️ 下载不稳定（record-dependent）

semantic / core / citeseerx / doaj / base / zenodo / hal / openaire — 取决于具体记录是否有 OA PDF，CS 领域覆盖率低。

### 🎯 仅使用 arxiv（推荐配置）

对于计算机科学领域的论文检索，**建议只启用 arxiv**，避免返回大量无法下载的元数据结果：

```dotenv
# .env — 仅使用 arxiv（其余全部禁用）
PAPER_SEARCH_MCP_DISABLED_SOURCES=pubmed,biorxiv,medrxiv,google_scholar,iacr,semantic,crossref,openalex,pmc,core,europepmc,dblp,openaire,citeseerx,doaj,base,zenodo,hal,ssrn,unpaywall
```

如需恢复多源搜索，将此行注释或改为空值即可。所有可选数据源列表见 `paper_search_mcp/engine/search.py` 中的 `ALL_SOURCES`。

### 🔑 MinerU API Key 配置弹窗

支持 MCP Apps 的客户端可调用 `mineru_setup_status` 检查配置。未配置 `PAPER_SEARCH_MCP_MINERU_API_KEY` 时返回 `mineru_api_key_prompt`，指向 `render_mineru_api_key_setup_app`，客户端据此显示输入弹窗。Widget 提交后调用 `configure_mineru_api_key`，将 key 写入 `.env`。

解析时若检测到 key 缺失/过期/401/403 等鉴权错误，返回结果会附带 `mineru_api_key_prompt` 提示重新配置。

说明：MCP Server 可以提供 Apps UI 和提示数据，但“安装 MCP 后是否自动弹窗”由具体 MCP Host 决定。支持启动/安装后自动调用工具的 Host 应调用 `mineru_setup_status`；不支持的 Host 仍可由 Agent 在首次解析或健康检查时打开配置 UI。

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

检索默认 `fast` profile。需要更全覆盖时传 `sources="deep"` 或 `sources="all"`。`SEARCH_TIMEOUT_SECONDS`、`SEARCH_SOURCE_TIMEOUT_SECONDS`、`SEARCH_CACHE_TTL_SECONDS` 分别控制整体/单源超时和缓存。

批量解析用 `PARSE_CONCURRENCY` 控制并发。`mode="extract"`/`"cloud_api"` 优先走 MinerU 多文件 batch；`auto` 模式设 `MINERU_BATCH_PARSE=true` 启用真批处理。`MINERU_UPLOAD/DOWNLOAD_CONCURRENCY` 调上传下载并发。`MINERU_AUTO_ORDER` 控制 auto 顺序。设 `MINERU_EXPORT_ZIP=true` 生成同名 zip。

---

## 📁 默认保存位置

当前默认 PDF 保存路径是：

```text
~/Desktop/papers
```

在 Windows 上会解析为当前用户桌面，例如：

```text
C:\Users\<你的用户名>\Desktop\papers
```

你也可以在 CLI 或 MCP 工具参数中传入 `save_path` 覆盖默认位置。

---

## 🧪 MinerU 解析输出

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

---

## 🔌 MCP 使用流程

### 1. 🚀 自然语言首选高层入口

对 VS Code Copilot Agent Mode、Claude Desktop、Claude Code 等 MCP Host，推荐优先调用 `paper_research_workflow`，它把检索、排序、下载和解析任务都留在 MCP 内完成，避免 Agent 打开终端或执行 CLI 命令。

常用参数：`intent="search_only"` 仅检索；`intent="search_download"` 检索并下载；`intent="search_download_parse"` 检索下载并后台解析；`selection_mode="manual"` 先选后下载；`parse_execution="background"` 后台解析（返回 job_id）；`parse_execution="sync"` 同步等待解析；`parse_execution="none"` 只下载不解析。`ranking_profile="agent-skill"` 适用 LLM Agent/Skill 领域。

除非用户明确指定目录，不要传 `save_path`；默认保存到 `~/Desktop/papers`。

> 提示词示例见 [MCP 使用示例提示词.md](MCP使用示例提示词.md)

### 2. 🔍 只检索论文

适合只想拿到论文条目、标题、作者、DOI、PDF 链接等元数据的场景。

> 提示词示例见 [MCP 使用示例提示词.md](MCP使用示例提示词.md) 场景 2。

### 3. ✅ 检索后弹出多选解析

如果 MCP 客户端支持 Elicitation，例如 VS Code Copilot Agent Mode，并且客户端把 `array + enum` 渲染为多选控件，就可以看到 checkbox 或多选列表。用户选择论文后，MCP Server 会下载并解析所选条目。

> 提示词示例见 [MCP 使用示例提示词.md](MCP使用示例提示词.md) 场景 3。

### 3. 🖥️ MCP Apps checkbox UI

支持 MCP Apps 的客户端可以打开独立 checkbox 组件，适合检索候选论文后手动选择，或单次保存超过 10 篇 PDF 后选择要解析的论文。勾选论文后走 `download_selected_papers`（仅下载）或 `download_and_parse_selected_papers`（下载并解析）。

如当前 Host 不能渲染 MCP Apps 但允许打开系统浏览器，可用 `open_paper_selection_page` 启动 localhost 页面并用系统浏览器打开，提交后同样调用下载或下载+解析工具。

### 4. 🔢 无 checkbox UI 时的编号选择

先生成后端选择 session，获得 `selection_token` 和编号后的论文列表，然后按编号解析。

`selected_indices` 支持：

- `"all"`：解析全部可解析论文。
- `"1,3,5"`：解析指定编号。
- `"2-4"`：解析一个连续范围。

### 5. ⚡ 保存 PDF 后自动解析 / 大批量选择

以下 MCP 工具路径只要实际保存了 PDF，就会返回 `parse_prompt` 或 `saved_pdf_prompt`：

- 各平台 `download_*` 工具。
- 各平台 `read_*` 工具中发生 PDF 下载的情况。
- `download_with_fallback`。

保存后的处理规则：

- 单篇/≤10 篇自动解析所有可解析 PDF（`parse_prompt.interaction` 为 `auto_parse_saved_pdfs`）。
- `download_selected_papers` 批量下载：≤10 篇自动提交后台解析；>10 篇先返回 checkbox/编号选择，用户确认后走 `download_and_parse_selected_papers` 下载选中的 PDF 并立即启动 MinerU 解析。

### 6. 📄 直接解析本地 PDF

如果你已经有本地 PDF，可以直接调用 `parse_pdf_with_mineru`。

解析完成后在 PDF 同目录生成 `example_mineru/`，开启 `PAPER_SEARCH_MCP_MINERU_EXPORT_ZIP=true` 时额外生成同名 zip。`.paper_search_cache` 只保留轻量索引。

---

## 📝 MCP 使用提示词

快速上手示例，直接粘贴到 Claude Code / VS Code Copilot 中使用：

```text
# 一站式：搜索 LLM agent skill 论文 → 下载 PDF → 后台解析
用 paper-search-mcp 搜索 5 篇 "LLM agent skill" 论文，下载并后台解析。

# 只看不下载：先浏览再决定
用 paper-search-mcp 搜索 "multi-agent reinforcement learning"，
只看结果不下载。

# 按年份过滤 + 手动挑选
用 paper-search-mcp 搜索 "federated learning" 2024 年的论文，
让我手动挑选后再下载解析。

# 已知 arXiv ID 直接下载
用 paper-search-mcp 下载 arXiv 论文 2301.12345。

# 批量获取出版商正式版（Nature/Elsevier/Springer）
用 paper-search-mcp 批量下载这些论文的出版商版本：
arxiv_1706.03762, arxiv_1810.04805

# 解析本地已有 PDF
用 paper-search-mcp 解析 ~/Desktop/paper.pdf，mode='auto'。

# 全文搜索已解析的论文
用 paper-search-mcp 在我已解析的论文中搜索 "attention mechanism"。
```

完整的场景化提示词模板、参数说明和源配置速查已整理为独立文档：

> 📖 **[MCP 使用示例提示词.md](MCP使用示例提示词.md)** — 包含 11 个场景的精确提示词、`paper_research_workflow` / `download_publisher_version` 等工具的参数表、搜索源配置速查。

---

## 🧰 常用 MCP 工具

- `search_papers`：多源检索并去重。
- `paper_research_workflow`：自然语言 Agent 首选高层入口，可一次完成检索、下载，并按需提交后台解析任务。
- `search_papers_with_elicitation`：检索后通过 Elicitation 请求用户多选论文并解析。
- `search_papers_for_parsing`：检索并创建编号选择 session。
- `render_paper_selection_app`：为支持 MCP Apps 的客户端渲染 checkbox 论文选择器。
- `open_paper_selection_page`：为不支持 MCP Apps 的客户端打开本地浏览器 checkbox 选择页。
- `download_selected_papers`：根据 selection session 批量下载论文，写 manifest；大批量会先返回 checkbox 选择提示，明确选择后才下载。
- `download_and_parse_selected_papers`：用户在 checkbox/编号流程明确选择后，下载选中的 PDF，并立即提交 MinerU 后台解析任务。
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
- `download_publisher_version`：通过 scansci-pdf MCP Chaining 下载 arXiv 论文的出版商最终发行版 PDF（单篇）。
- `batch_download_publisher_versions`：批量下载多篇 arXiv 论文的出版商发行版。
- `check_publisher_setup`：检查 scansci-pdf 环境（安装状态、Tor、CloakBrowser、API Key 配置）。

---

## 🎬 典型使用场景

以下列出各场景的推荐工具调用方式。完整提示词见 [MCP 使用示例提示词.md](MCP使用示例提示词.md)。

### 🔍 快速找论文（只看不下载）

**推荐方式**：`paper_research_workflow(intent="search_only")` 或直接调用 `search_papers`。

### 🚀 找论文 + 下载 + 解析（全自动）

**推荐方式**：`paper_research_workflow(intent="search_download_parse", parse_execution="background")`，返回 `job_id` 后用 `get_parse_job_status` 查询。

### ✅ 找论文 + 手动选择后再解析

**推荐方式**：`paper_research_workflow(selection_mode="manual")` 或 `search_papers_for_parsing` + checkbox/编号。

### ⚡ 保存 PDF 后自动解析 / 大批量选择

调用单篇 `download_*`、`download_with_fallback` 或 `download_selected_papers` 时，≤10 篇可自动提交 MinerU 后台解析；>10 篇先返回 checkbox 或编号 fallback，不会提前下载。用户提交选择后，下载-only 只下载；下载并解析会走 `download_and_parse_selected_papers`，下载选中 PDF 后立即启动 MinerU。

### 🖥️ 客户端 UI 适配

- **支持 MCP Apps**：`search_papers_for_parsing` + `render_paper_selection_app`（checkbox 组件）。
- **不支持 MCP Apps 但可开浏览器**：`open_paper_selection_page`（localhost checkbox 页面）。
- **纯文本无 UI**：`search_papers_for_parsing` + `download_and_parse_selected_papers`、`submit_parse_job` 或 `parse_selected_papers`。

### 📄 解析已有本地 PDF

**推荐方式**：`parse_pdf_with_mineru` 或 `parse_pdfs_with_mineru`。

### 📖 查看已解析论文

**推荐方式**：`list_parsed_papers` / `get_parsed_paper` / `get_paper_assets`。

### 🔎 搜索已解析内容（构建知识库）

**推荐方式**：`search_parsed_papers` / `search_parsed_paper`。

### 🩺 环境配置与诊断

**推荐方式**：`mineru_setup_status` / `mineru_health_check` / `diagnose_paper_sources` / `check_publisher_setup`。

### 💾 缓存维护

**推荐方式**：`cleanup_stale_cache_entries` / `cleanup_redundant_cache_artifacts` / `index_parsed_cache`。

### 🏢 获取出版商发行版 PDF（进阶）

将缓存的 arXiv 论文升级为出版商最终发行版（Nature、Elsevier、Springer 等正式排版版本）。通过内置 scansci-pdf MCP Chaining 自动调用反检测浏览器、Tor 代理和 13+ 下载源。

**首次使用自动安装**，无需手动配置。`download_publisher_version` 内部自动启动 Tor、检查 CloakBrowser、探测可用源。如需更好的成功率，可配置免费 Elsevier API Key（1-2s 直下 ScienceDirect 论文）。

**推荐工具**：`download_publisher_version`（单篇）、`batch_download_publisher_versions`（批量）、`check_publisher_setup`（诊断）。

> 💡 **工作原理**：paper-search-mcp 通过 MCP Chaining 技术，在工具内部启动 scansci-pdf 子进程，提取论文的 DOI 或 arXiv ID 后调用 `scansci_pdf_smart_download`。scansci-pdf 并发尝试出版商直链、Elsevier API、Unpaywall、Sci-Hub 等 13+ 源，返回成功下载的 PDF。scansci-pdf 首次使用自动通过 pip 安装，Tor 自动下载配置，完全无感。

### 🔬 排查解析质量

优先检查 `<pdf 文件名>_mineru/manifest.json`，确认使用的后端是 `extract`、`local_api`、`cli` 还是 `pypdf`。可调用 `get_download_health_stats` 查看下载健康统计了解各渠道成功率。

---

## 🛡️ 合规与安全提示

- 项目默认采用开放获取优先策略，建议优先使用来源原生 PDF、开放仓储和 Unpaywall 等路径。
- Sci-Hub 属于可选能力，不应作为默认下载路径；是否启用以及如何使用由用户自行承担责任。
- 不要把 `PAPER_SEARCH_MCP_MINERU_API_KEY`、Semantic Scholar key、CORE key 等真实凭据写入 README、提交记录或公开 issue。
- `.env` 适合保存本地凭据，发布前应确认 `.gitignore` 已忽略该文件。

---

## 🧪 测试

常用回归测试命令：

```powershell
python -m unittest tests.test_selection_sessions tests.test_mineru_parser tests.test_fallback tests.test_config_env tests.test_server
```

文档修改本身不需要运行完整测试；当修改解析、下载、Elicitation 或缓存逻辑时，建议至少运行以上测试。

---

## 📜 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。

---

## 🙏 致谢

本项目的 fork、功能补齐和设计参考了以下 GitHub 仓库：

- [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp)：原始上游项目，提供多源论文检索、下载和 MCP Server 基础能力。
- [Dictation354/paper-fetch-skill](https://github.com/Dictation354/paper-fetch-skill)：提供论文抓取、下载和 Agent 使用流程方面的参考。
- [Rimagination/scansci-pdf](https://github.com/Rimagination/scansci-pdf)：提供科学 PDF 处理和解析工作流方面的参考。
- [yilewang/llm-for-zotero](https://github.com/yilewang/llm-for-zotero)：提供将 MinerU PDF parsing 接入论文阅读/管理流程的实现思路参考。
- [opendatalab/MinerU](https://github.com/opendatalab/MinerU)：提供高质量 PDF 解析、Markdown/JSON/资源抽取能力，是本项目 MinerU 解析链路的核心依赖方向。
- [mcp-use/mcp-use](https://github.com/mcp-use/mcp-use)：提供 MCP 服务架构与最佳实践参考。
