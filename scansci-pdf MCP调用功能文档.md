# scansci-pdf MCP 调用功能文档

> **模块**: [paper_search_mcp/tools/publisher.py](paper_search_mcp/tools/publisher.py)
> **上游依赖**: scansci-pdf v1.6.1 ([源码](https://github.com/Rimagination/scansci-pdf))
> **更新日期**: 2026-06-26

---

## 一、功能概述

`publisher.py` 模块实现了 paper-search-mcp 与外部 **scansci-pdf** MCP 服务器的链式调用，用于下载已在 paper-search-mcp 缓存中的 arXiv 论文的 **出版商最终发行版**（非 arXiv 预印本）。

### 核心流程

```
用户请求 → paper-search-mcp (FastMCP Server)
              │
              ├─ 1. 解析 paper_key 获取论文元数据 (cache)
              ├─ 2. 如果缓存中不存在, 自动下载 arXiv 预印本
              ├─ 3. 自动安装 scansci-pdf (如未安装)
              ├─ 4. 启动 scansci-pdf 子进程 (StdioTransport, keep_alive)
              ├─ 5. 执行环境初始化 (auto_setup + tor_start)
              └─ 6. 调用 scansci_pdf_smart_download
                    │
                    ▼
              scansci-pdf MCP Server (独立进程)
              ├─ Phase 1: Free sources — 并行竞速 (15s)
              │   ├─ OA: EuropePMC, Unpaywall, CORE, SemanticScholar, OpenAlex, CrossRef
              │   ├─ Direct: arXiv, DOAJ, ScibBan
              │   └─ Grey: Sci-Hub, LibGen
              ├─ Phase 2: Institutional access — 浏览器策略 (30s)
              │   ├─ Publisher browsers: Elsevier, Wiley, IEEE, ACS, RSC, AIP,
              │   │   Springer, APS, TandF, IOP, Oxford, ACM, Nature, Science,
              │   │   SAGE, ASCE, RoyalSociety, Copernicus (18+ 出版商)
              │   ├─ WebVPN / EZproxy / CARSI 联邦认证
              │   └─ Elsevier API (ScienceDirect 快速通道)
              └─ 返回最终 PDF 路径 + 下载源
```

### paper-search-mcp 暴露的 MCP 工具

| 工具名 | 类型 | 说明 |
|--------|------|------|
| `download_publisher_version` | 单篇下载 | 下载单篇缓存论文的出版商版本 |
| `batch_download_publisher_versions` | 批量下载 | 批量下载多篇论文（逗号分隔或 `"all"`）|
| `check_publisher_setup` | 环境诊断 | 检查 scansci-pdf 安装、组件、浏览器状态 |

---

## 二、架构设计

### 2.1 模块级状态管理

```python
# 懒加载的单例 Client，keep_alive=True 保持子进程存活
_scansci_client: Optional[Any] = None       # fastmcp.Client 实例

# 状态标记
_scansci_error: str = ""                    # 最近一次错误信息
_scansci_setup_done: bool = False           # auto_setup + tor_start 是否完成
_scansci_install_attempted: bool = False    # 是否已尝试过 pip install
_scansci_importable: Optional[bool] = None  # 三态: None=未检查, True/False
_component_status: Dict[str, bool] = {}     # 组件可用性缓存
```

### 2.2 连接生命周期

```
第一次调用 _get_scansci_client():
  1. 检查 _scansci_importable 缓存
  2. 如果为 None → import scansci_pdf 尝试
  3. 如果不可导入 → _auto_install_scansci_pdf()
     ├─ pip install scansci-pdf[cloakbrowser]  (uv pip 自动回退)
     ├─ pip install scansci-pdf (回退)
     ├─ 检测并安装缺失组件 (CloakBrowser, SOCKS, Crypto)
     └─ Playwright Chromium 浏览器检测
  4. 创建 StdioTransport → 启动 python -m scansci_pdf.main run
  5. 包装为 fastmcp.Client (keep_alive=True)

后续调用:
  - _get_scansci_client() 直接返回缓存的 client
  - _ensure_scansci_ready() 只在 _scansci_setup_done=False 时执行 (一次性)

崩溃恢复 (P1-2 已修复):
  - 捕获 McpError/BrokenResourceError/ConnectionClosed
  - 重置 _scansci_client = None + _scansci_setup_done = False
  - 下次调用创建全新连接
  - 智能检测 Playwright 缺失并给出精准提示
```

### 2.3 scansci-pdf 下载流水线（上游实现）

scansci-pdf 内部采用**两阶段并行竞速**架构：

**Phase 1 — Free sources (15s 超时)**：
```
并行竞速: EuropePMC │ Unpaywall │ CORE │ SemanticScholar │ OpenAlex
          CrossRef │ arXiv │ DOAJ │ ScibBan │ Sci-Hub* │ LibGen*

* Sci-Hub 和 LibGen 可通过 Tor SOCKS5 代理访问
```

**Phase 2 — Institutional access (30s 超时)**：
```
出版商浏览器策略 (CloakBrowser + Playwright):
  Elsevier → Wiley → IEEE → ACS → RSC → AIP → Springer → APS
  → TandF → IOP → Oxford → ACM → Nature → Science → SAGE
  → ASCE → RoyalSociety → Copernicus

机构网关:
  WebVPN (300+ 中国高校) → EZproxy → CARSI/Shibboleth 联邦认证

API 快速通道:
  Elsevier API Key → ScienceDirect 全文 (1-2s)
```

**下载源竞速控制**：
- 浏览器类源（18+ 出版商）使用 `Semaphore` 限制并发，防止 Chrome 窗口爆炸
- `ThreadPoolExecutor` 实现 Tier 内并行竞速
- 任何源先成功即返回，其余自动取消

### 2.4 scansci-pdf MCP Server 完整工具列表

| # | 工具名 | 功能 | 我们的使用 |
|---|--------|------|-----------|
| 1 | `scansci_pdf_smart_download` | 零配置下载（竞速所有源） | ✅ 核心调用 |
| 2 | `scansci_pdf_download` | 下载（可精确控制源） | ❌ 未使用 |
| 3 | `scansci_pdf_batch_download` | 批量下载（支持 progress/resume） | ❌ 未使用* |
| 4 | `scansci_pdf_search` | OpenAlex 论文搜索 | ❌ |
| 5 | `scansci_pdf_health_check` | 下载源可用性 + 延迟检测 | ✅ check_publisher_setup 中 |
| 6 | `scansci_pdf_browser_doctor` | 浏览器运行时诊断 | ❌ 未使用 |
| 7 | `scansci_pdf_source_scores` | 自适应源健康度评分 (EMA) | ❌ 未使用 |
| 8 | `scansci_pdf_auto_setup` | 一键初始化 (Tor/浏览器/Sci-Hub) | ✅ _ensure_scansci_ready 中 |
| 9 | `scansci_pdf_elsevier_setup` | Elsevier API Key 配置指引 | ❌ 未使用 |
| 10 | `scansci_pdf_network_diagnose` | 网络连通性诊断 | ❌ 未使用 |
| 11 | `scansci_pdf_config_get` | 获取 scansci-pdf 配置 | ❌ |
| 12 | `scansci_pdf_config_set` | 修改 scansci-pdf 配置 | ❌ |
| 13 | `scansci_pdf_cache_clear` | 清除下载缓存 | ❌ |
| 14 | `scansci_pdf_import_bib` | 从 .bib 文件批量导入下载 | ❌ |
| 15 | `scansci_pdf_citation` | 获取论文引用 (BibTeX/RIS/EndNote) | ❌ |
| 16 | `scansci_pdf_paper_metadata` | 获取论文元数据 | ❌ |
| 17 | `scansci_pdf_zotero_push` | 推送到 Zotero | ❌ |
| 18 | `scansci_pdf_instsci_login` | WebVPN 机构代理登录 | ❌ |
| 19 | `scansci_pdf_instsci_test` | 测试 WebVPN 连通性 | ❌ |
| 20 | `scansci_pdf_instsci_status` | WebVPN 状态查询 | ❌ |
| 21 | `scansci_pdf_instsci_schools` | 搜索支持的学校列表 | ❌ |
| 22 | `scansci_pdf_carsi_login` | CARSI 联邦认证登录 | ❌ |
| 23 | `scansci_pdf_carsi_status` | CARSI 状态查询 | ❌ |

> **\* 重要发现**: `scansci_pdf_batch_download` 支持 `resume=True`（断点续传）和 `progress_callback`（进度回调，支持 MCP `ctx.report_progress`），这正是我们批量下载缺失的功能。当前 `batch_download_publisher_versions` 是逐个调用 `smart_download`，效率低于使用上游的批量 API。

### 2.5 自适应源评分系统

scansci-pdf 实现了 `sources/scoring.py` 模块，使用 **EMA（指数移动平均）**跟踪每个下载源的成功率和延迟：

```
每次下载后更新:
  success_ema = α × (1 if success else 0) + (1-α) × previous_success_ema
  latency_ema = α × latency_ms + (1-α) × previous_latency_ema

低评分源在后续下载中被自动降优先级
```

可通过 `scansci_pdf_source_scores` 工具查询。**我们目前未将此信息暴露给用户**。

---

## 三、已完成的 P0+P1 优化

### 3.1 P0-1: venv 缺少 pip 导致自动安装失败 ✅

**问题**: 项目使用 `uv` 创建虚拟环境，默认不包含 `pip` 模块。原有代码 `sys.executable -m pip install` 失败时只有模糊的 `CalledProcessError`。

**修复**: 新增 `_run_pip_install()` 辅助函数 ([publisher.py:174-224](paper_search_mcp/tools/publisher.py#L174-L224))
- 先尝试标准 `python -m pip install`
- 如果 stderr 包含 `"No module named pip"`，自动回退到 `uv pip install`
- 所有调用点均已替换为使用此函数

### 3.2 P0-2: scansci-pdf 未声明为可选依赖 ✅

**问题**: scansci-pdf 不在 `pyproject.toml` 中，用户无法预装。

**修复**: 在 [pyproject.toml:44-47](pyproject.toml#L44-L47) 添加：
```toml
[project.optional-dependencies]
publisher = ["scansci-pdf[cloakbrowser]"]
```

### 3.3 P1-1: Playwright Chromium 浏览器状态未检测 ✅

**问题**: CloakBrowser 依赖 Playwright + Chromium (~182MB)，缺失时 scansci-pdf 静默崩溃。

**修复**: 新增 `_check_playwright_browser()` ([publisher.py:75-118](paper_search_mcp/tools/publisher.py#L75-L118))，集成到组件检测和 `check_publisher_setup`。

### 3.4 P1-2: scansci-pdf 崩溃后客户端未重置 ✅

**问题**: scansci-pdf 子进程崩溃时，`_scansci_client` 未置空，后续调用复用死连接。

**修复**: 崩溃时重置 `_scansci_client = None` + `_scansci_setup_done = False`，并智能检测 Playwright 缺失给出精准提示。

---

## 四、仍存在的问题

### 🔴 P2-1: scansci-pdf 调用缺少重试机制

**位置**: [publisher.py:827-838](paper_search_mcp/tools/publisher.py#L827-L838)

**分析**: `_download_arxiv_preprint` 有 3 次重试 + 退避，但 scansci-pdf 调用一次失败即返回。阅读 scansci-pdf 源码确认：其内部的 `_try_source` 单源不重试，`_run_tier` 也只竞速一次。网络抖动时所有源可能暂时不可达。

**建议修复**:
```python
# 在 _download_one_publisher_version 中调用 scansci_pdf_smart_download 时添加重试
MAX_RETRIES = 3
for attempt in range(MAX_RETRIES):
    try:
        async with client:
            result = await asyncio.wait_for(
                client.call_tool("scansci_pdf_smart_download", {...}),
                timeout=timeout,
            )
        break
    except asyncio.TimeoutError:
        if attempt == MAX_RETRIES - 1: return {"status": "timeout", ...}
        await asyncio.sleep(2 ** attempt)
    except Exception:
        if attempt == MAX_RETRIES - 1: raise
        await asyncio.sleep(2 ** attempt)
```

### 🟡 P2-2: 异常类型检测使用字符串比较

**位置**: [publisher.py:855](paper_search_mcp/tools/publisher.py#L855)

**分析**: `type(exc).__name__ in ("McpError", ...)` 在依赖库重构时可能失效。

**建议修复**: 改为 `isinstance(exc, (McpError, BrokenResourceError, ClosedResourceError))`。

### 🟡 P2-3: 批量下载应改用 scansci-pdf 的批量 API

**位置**: [publisher.py:1108-1129](paper_search_mcp/tools/publisher.py#L1108-L1129)

**分析**: 当前实现逐个调用 `scansci_pdf_smart_download`，但 scansci-pdf 提供了 `scansci_pdf_batch_download`（[server.py:128-181](C:\code\paper-search-mcp\.venv\Lib\site-packages\scansci_pdf\server.py#L128-L181)）：
- 原生支持 `resume=True` — 跳过已完成的项
- 原生支持 `progress_callback` — 包括 MCP `ctx.report_progress`
- 统一的 `batch_id` 管理
- 更好的错误聚合（区分 paywall/not_found/network_error）

**建议修复**: 重构 `batch_download_publisher_versions` 使用 `scansci_pdf_batch_download`。

### 🟡 P2-4: 下载期间缺少进度反馈

**分析**: scansci-pdf 的 `scansci_pdf_batch_download` 已支持 `ctx.report_progress`，但我们当前调用 `smart_download` 无法利用此能力。

**建议**: 至少添加 `logger.info` 心跳；理想方案是使用批量 API 获取进度回调。

### 🟡 P2-5: 环境初始化与下载共享超时预算

**位置**: [publisher.py:807-838](paper_search_mcp/tools/publisher.py#L807-L838)

**分析**: `_ensure_scansci_ready` 的 setup 超时（默认 10s+5s）不在用户设置的 `timeout` 参数控制范围内。

**建议修复**: 整体超时 = `total_timeout`，内含 setup + download，或单独暴露 `setup_timeout` 参数。

### 🟡 P2-6: `.doi_index.json` 删除过于粗暴

**位置**: [publisher.py:814-824](paper_search_mcp/tools/publisher.py#L814-L824)

**分析**: 每次调用删除整个索引文件。同一 save_path 的多篇论文会互相破坏索引。阅读 scansci-pdf 源码（[sources/__init__.py:584-605](C:\code\paper-search-mcp\.venv\Lib\site-packages\scansci_pdf\sources\__init__.py#L584-L605)）发现其 `.doi_index.json` 设计为按 DOI 索引，删除整文件过于粗暴。

**建议修复**: 删除索引中当前 DOI 的单条记录，而非整个文件。

### 🟡 P2-7: 缺少下载恢复/断点续传

**分析**: `scansci_pdf_batch_download` 已原生支持 `resume=True`。改用此 API 可天然获得恢复能力。

### 🟢 P2-8: 批量模式未区分失败原因

**位置**: [publisher.py:1125-1129](paper_search_mcp/tools/publisher.py#L1125-L1129)

**建议修复**: 在返回结果中增加 `failed_by_reason` 分类统计（paywall / not_found / timeout / network_error）。

### 🟢 P2-9: 文档与工具描述未同步更新

---

## 五、基于上游源码的新发现与优化机会

### 5.1 未利用的上游能力

通过对 scansci-pdf v1.6.1 完整源码的阅读，发现以下**高价值但未使用**的功能：

| 发现 | 来源 | 价值 | 建议 |
|------|------|------|------|
| 批量下载 API 支持 resume+progress | `server.py:128` | **极高** — 天然解决 P2-3/2-4/2-7 | P2-3 修复时采用 |
| 自适应源评分系统 (EMA) | `sources/scoring.py` | **高** — 可指导用户选择最优下载策略 | 在 `check_publisher_setup` 中暴露 |
| Elsevier API 快速通道 | `server.py:382` | **高** — ScienceDirect 论文 1-2s 下载 | 新增配置引导工具 |
| 网络诊断工具 | `server.py:485` | **中** — 精准定位网络问题 | 集成到 `check_publisher_setup` |
| 浏览器运行时诊断 | `server.py:277` | **中** — 浏览器问题精准定位 | 集成到 `check_publisher_setup` |
| 300+ 中国高校 WebVPN | `schools.py` | **中** — 机构用户核心价值 | 文档引导 |
| CARSI 联邦认证 | `sources/carsi.py` | **中** — 国际机构访问 | 文档引导 |
| 出版商覆盖率 18+ | `publisher_strategies.py` | **高** — 远超最初估计的"几个" | 更新文档宣传 |

### 5.2 下载源完整清单（共 30+）

**Free tier (12)**:
ArXiv, EuropePMC, PubMedCentral, CORE, SemanticScholar, OpenAlex, CrossRef, DOAJ, ScibBan, Sci-Hub, LibGen, Unpaywall

**Publisher Browser tier (18)**:
Elsevier, Wiley, IEEE, ACS, RSC, AIP, Springer, APS, TandF, IOP, Oxford, ACM, Nature, Science, SAGE, ASCE, RoyalSociety, Copernicus

**Institutional tier (4)**:
WebVPN (300+ universities), EZproxy, CARSI/Shibboleth, Elsevier API

---

## 六、优化方案（修订版）

### 6.1 短期优化（推荐立即实施）

| 优先级 | 编号 | 优化项 | 工作量 | 依赖 |
|--------|------|--------|--------|------|
| 🔴高 | P2-1 | 添加重试机制（3次+指数退避） | ~20行 | 无 |
| 🔴高 | P2-2 | `isinstance` 替换字符串异常匹配 | ~5行 | 无 |
| 🟡中 | P2-5 | setup 超时纳入总体 timeout | ~15行 | 无 |
| 🟡中 | P2-9 | 更新 docstring 和文档 | ~30行 | 无 |

### 6.2 中期优化（下个迭代）

| 优先级 | 编号 | 优化项 | 工作量 | 依赖 |
|--------|------|--------|--------|------|
| 🔴高 | **NEW** | `batch_download_publisher_versions` 改用 `scansci_pdf_batch_download` API | ~60行 | P2-3, P2-4, P2-7 一并解决 |
| 🟡中 | P2-6 | 细粒度 `.doi_index.json` 管理 | ~30行 | 无 |
| 🟡中 | **NEW** | `check_publisher_setup` 集成 `scansci_pdf_network_diagnose` + `scansci_pdf_browser_doctor` | ~40行 | 无 |
| 🟡中 | **NEW** | `check_publisher_setup` 暴露 `scansci_pdf_source_scores` 自适应评分 | ~20行 | 无 |
| 🟢低 | P2-8 | 批量模式失败原因分类统计 | ~25行 | P2-3 |

### 6.3 长期优化（架构级别）

1. **并发批量下载**: 使用 scansci-pdf 的 `batch_download` API 天然支持并行 + progress。用户不再需要等待逐个下载。

2. **Elsevier API 一键配置**: 新增 `configure_elsevier_api` MCP 工具，调用 `scansci_pdf_elsevier_setup` 并引导用户完成配置。ScienceDirect 论文下载速度从 30-120s 降至 1-2s。

3. **机构访问引导**:
   - 新增 `setup_institutional_access` MCP 工具
   - 调用 `scansci_pdf_instsci_schools` 搜索学校
   - 调用 `scansci_pdf_instsci_login` 打开浏览器完成 CAS 认证
   - 适用于 300+ 中国高校的 WebVPN 用户

4. **源健康度仪表板**: 在 `check_publisher_setup` 中展示各下载源的成功率 EMA、平均延迟、最近错误，帮助用户理解当前最优下载策略。

5. **流式进度上报**: 利用 `scansci_pdf_batch_download` 的 `ctx.report_progress` 机制，向 LLM Agent 实时报告下载进度。

6. **离线下载队列**: 允许用户提交 DOI 列表到后台队列，MCP server 空闲时自动尝试下载，利用 `scansci_pdf_batch_download` 的 `resume` 能力。

---

## 七、测试与验证

### 7.1 环境验证

```bash
# 检查 scansci-pdf 安装状态 (含 Playwright)
uv run python -c "from paper_search_mcp.tools.publisher import _detect_publisher_components; import json; print(json.dumps(_detect_publisher_components(), indent=2))"

# 检查 Playwright 浏览器
uv run python -c "from paper_search_mcp.tools.publisher import _check_playwright_browser; print(_check_playwright_browser())"

# 测试 pip/uv pip 回退
uv run python -c "from paper_search_mcp.tools.publisher import _run_pip_install; print(_run_pip_install('six', timeout=30))"
```

### 7.2 MCP 工具验证

```python
# 1. 环境诊断 (含 Playwright 状态)
check_publisher_setup()

# 2. 下载出版商版本
download_publisher_version(
    paper_key="arxiv_1706.03762",
    save_path="~/Desktop/papers",
    timeout=180
)

# 3. 批量下载
batch_download_publisher_versions(
    paper_keys="arxiv_1706.03762,arxiv_1810.04805",
    save_path="~/Desktop/papers",
    timeout=600
)
```

### 7.3 当前环境状态

| 检查项 | 状态 |
|--------|------|
| scansci-pdf 包 (venv) | ✅ 已安装 v1.6.1 |
| CloakBrowser | ✅ 已安装 v0.4.3 |
| Playwright Python | ✅ 已安装 v1.60.0 |
| Playwright Chromium | ⚠️ 需手动安装 (~182MB) |
| Tor / SOCKS | ⚠️ 未安装 (仅影响 Sci-Hub 访问) |
| pycryptodome | ⚠️ 未安装 (仅影响 WebVPN 加密) |
| Elsevier API Key | ❌ 未配置 |

---

## 八、模块接口速查

### 核心函数 (paper_search_mcp)

| 函数 | 签名 | 说明 |
|------|------|------|
| `_get_scansci_client()` | `() -> Optional[Client]` | 获取或创建 scansci-pdf 连接 |
| `_auto_install_scansci_pdf()` | `() -> bool` | 自动安装 scansci-pdf + 组件 |
| `_ensure_scansci_ready(client)` | `async (Client) -> dict` | 一次性环境初始化 |
| `_detect_publisher_components()` | `() -> Dict[str, Dict]` | 组件检测 (含 Playwright) |
| `_check_playwright_browser()` | `() -> Dict[str, Any]` | Playwright Chromium 检测 |
| `_run_pip_install(spec, timeout)` | `(str, int) -> bool` | pip/uv pip 带回退 |

### MCP 工具 (paper_search_mcp)

| 工具 | 关键参数 | 返回 |
|------|----------|------|
| `download_publisher_version` | `paper_key`, `save_path`, `timeout`, `force_reparse` | `{status, publisher_pdf, download_source, ...}` |
| `batch_download_publisher_versions` | `paper_keys`, `save_path`, `timeout`, `force_reparse` | `{total, ok, failed, results[]}` |
| `check_publisher_setup` | (无) | `{scansci_pdf_installed, components, playwright, client_available, health}` |

### MCP 工具 (scansci-pdf — 已在链式中使用)

| 工具 | 调用位置 | 用途 |
|------|----------|------|
| `scansci_pdf_smart_download` | `_download_one_publisher_version` | 核心下载 |
| `scansci_pdf_auto_setup` | `_ensure_scansci_ready` | 环境初始化 |
| `scansci_pdf_tor_start` | `_ensure_scansci_ready` | Tor 启动 |
| `scansci_pdf_health_check` | `check_publisher_setup` | 健康诊断 |

### 状态码速查

| status | 含义 |
|--------|------|
| `ok` | 下载成功 |
| `not_found` | paper_key 不存在于缓存 |
| `not_applicable` | 无 DOI 且无 arXiv ID |
| `unavailable` | scansci-pdf 安装/启动失败 |
| `download_failed` | 所有 30+ 下载源均失败 |
| `timeout` | 下载超时 |
| `invalid_pdf` | 下载了非 PDF 文件 |
| `error` | 其他错误 (含子进程崩溃) |

---

## 九、相关文件索引

| 文件 | 说明 |
|------|------|
| [paper_search_mcp/tools/publisher.py](paper_search_mcp/tools/publisher.py) | **主模块** (~1233行)，全部 publisher 逻辑 |
| [paper_search_mcp/server.py:2971-2979](paper_search_mcp/server.py#L2971-L2979) | 工具注册入口 |
| [pyproject.toml:44-47](pyproject.toml#L44-L47) | publisher 可选依赖声明 |
| [.env.example](.env.example) | Publisher 环境变量文档 |
| `.venv/Lib/site-packages/scansci_pdf/server.py` | **上游** MCP 工具定义 (21+ tools) |
| `.venv/Lib/site-packages/scansci_pdf/sources/__init__.py` | **上游** 下载竞速引擎 |
| `.venv/Lib/site-packages/scansci_pdf/publisher_strategies.py` | **上游** 18+ 出版商浏览器策略 |
| `.venv/Lib/site-packages/scansci_pdf/sources/scoring.py` | **上游** 自适应源评分 (EMA) |
| `.venv/Lib/site-packages/scansci_pdf/setup.py` | **上游** 系统环境诊断 |
