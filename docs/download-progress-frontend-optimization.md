# 下载后台 Job + 真实进度条：前端优化整体方案

> 适用：paper-search-mcp（`paper_search_mcp/`）
> 状态：方案设计稿，待评审后分阶段实施
> 前置审计：已完成的 workflow 审计 + 逐文件精读（见文末「关联审计结论」）

---

## 一、背景与目标

当前前端（本地浏览器 checkbox 选择页 + MCP 资源 widget）在**下载**和 **MinerU 解析**两个阶段的进度展示严重不一致：

| 阶段 | 现状 | 是否实时 |
|---|---|---|
| MinerU 解析 | 真正的后台 job + SSE 实时推送 | ✅ 实时 |
| 下载 | 同步阻塞 + 前端假计时器动画 | ❌ 假进度 |

**目标**：把「下载」改造为与「解析」同构的**后台 job + 实时进度**，让浏览器进度条真实反映每篇论文的下载状态；可选地，为单个大 PDF 提供字节级真实百分比。

---

## 二、现状诊断（基于代码审计）

### 2.1 解析链路（实时，作为改造模板）

解析已经是一条完整、可复用的「后台 job + SSE」管线：

1. `submit_parse_job`（`tools/core.py:374`）创建 `job_id` + daemon 线程，立即返回。
2. 线程跑 `_run_parse_selected_papers`，在每篇论文的阶段切换处调用 `_update_parse_job_item(job_id, index, status=…)`（`tools/core.py:204/239/281/301/326`，阶段为 downloading→ready→parsing→completed）。
3. 每次更新调 `_progress_notify(job_id, snapshot)`（`engine/jobs.py:225`），把快照推入进程内订阅队列。
4. 浏览器 `EventSource("/api/progress-stream/<job_id>")`（`server.py:673`，`ui/html_templates.py:1576/2646`）接收 SSE 帧。
5. 兜底轮询 1s（`get_parse_job_status`，`html_templates.py:1605`；`/api/parse-job`，`:2666`）。

> 关键点：`job_id`、daemon 线程、`_update_parse_job_item`、`_progress_notify`、SSE 端点、轮询兜底——**这 6 个组件全部已存在且可复用**。

### 2.2 下载链路（假进度，问题所在）

下载当前是**同步**的：

1. 浏览器勾选提交 → POST 到本地选择页服务 → `asyncio.run(_run_download_selected_papers(...))`（`server.py:511` 附近）**阻塞直到全部下载完成**。
2. 下载循环在 `_run_download_selected_papers` 的 `gather` 里跑（`orchestration.py:1677` 附近），但**不创建 job_id、不发布任何进度**。
3. 前端只能用一个 `startDownloadProgress` 假计时器（`html_templates.py:1308/2401`）假装有进度。

后果：浏览器端进度条与实际下载脱节；下载大量论文时前端无任何真实反馈。

### 2.3 SSE 健壮性（已加固）

`engine/jobs.py` 的 `_progress_notify` 原用 `queue.Queue(maxsize=64) + put_nowait`，慢客户端会**丢帧**（包括终态帧）。**已改为队列满时合并保留最新快照**（drop 最旧一帧再放入），保证终态永不丢失。

---

## 三、目标架构

让「下载」复用「解析」的 job 机制，形成对称的两条管线：

```
浏览器勾选 → POST /download-selected
                │
                ▼
        创建 job_id + daemon 线程（立即返回 {status:"submitted", job_id}）
                │
                ▼
        后台逐篇下载，每篇调用 _update_parse_job_item(job_id, i, status="downloading"/"downloaded"/"download_failed")
                │
                ▼
        _progress_notify(job_id, snapshot) ──► SSE /api/progress-stream/<job_id> ──► 浏览器 EventSource
                │                                        └─► 1s 轮询兜底
                ▼
        终态 snapshot 推给所有订阅者
```

**核心原则**：下载和解析共用同一套 `job_id` / `_update_parse_job_item` / `_progress_notify` / SSE 端点，只差「阶段枚举」不同（下载没有 `parsing` 阶段）。

---

## 四、详细改动方案

### 4.1 后端：下载 job 化（核心）

**文件**：`tools/orchestration.py`（下载循环）、`engine/jobs.py`（复用，基本不用改）

- 新增一个 `submit_download_job(selection_token, selected_indices, ...)` 入口，仿照 `submit_parse_job`（`tools/core.py:374`）：
  1. 校验 >10 篇的 selection 确认（复用 `_should_require_large_batch_selection`）。
  2. 创建 `job_id`，登记到 `jobs.py` 的 job 表（`job_id` 可复用 parse 的存储结构，或独立一个 `download` 命名空间）。
  3. 启动 daemon 线程跑 `_run_download_selected_papers`，并给循环注入逐篇回调。
- 在 `_run_download_selected_papers` 的 `gather` 循环里（`orchestration.py:1677` 附近），每篇论文的下载前/成功后调用 `_update_parse_job_item(job_id, index, status=...)`：
  - 开始 → `status="downloading"`（或复用 `downloading`/`ready` 枚举）
  - 成功 → `status="downloaded"` + `pdf_path`
  - 失败 → `status="download_failed"` + 错误信息
- 阶段映射：`_parse_job_stage_progress`（`jobs.py:58`）目前是解析专用（5/15/35/45/70/100）。下载可复用「downloading=5、ready/完成=100」两档，或为下载新增一个更细的阶段表。**建议先复用最小枚举**（downloading→downloaded），避免动 `_parse_job_stage_progress` 的解析语义。

**关键**：`_update_parse_job_item` 和 `_progress_notify` 是通用 job 基建，下载直接复用即可，**不要**新造一套下载专用进度。

### 4.2 后端：HTTP 端点异步化

**文件**：`server.py`（本地选择页 POST 处理器，`server.py:511` 附近）

- 把 `asyncio.run(_run_download_selected_papers(...))` 的**同步阻塞**改为：先创建下载 job（返回 `job_id`），POST 响应立即返回 `{status:"submitted", job_id}`。
- 保留 `/api/progress-stream/<job_id>`（已存在）供前端 SSE 订阅；`get_parse_job_status`/`/api/parse-job` 端点需要能查下载 job（或新增一个通用的 `get_job_status`）。

### 4.3 前端：JS 接 SSE

**文件**：`ui/html_templates.py`

- 删掉 `startDownloadProgress` 假计时器（`:1308/2401`）。
- 替换为与解析相同的 `connectSSE(job_id)` / `connectProgress(job_id)`：`EventSource("/api/progress-stream/<job_id>")` 收到 `downloaded` 事件即刷新该行状态；`onerror` 降级到 1s 轮询 `get_parse_job_status`（`html_templates.py:1605` 已有此兜底逻辑）。
- 进度条进度 = `已完成篇数 / 总篇数`，每篇一行展示 `downloading`/`downloaded`/`download_failed` 状态。

### 4.4 可选：字节级进度（单篇真实百分比）

**文件**：`engine/download.py`（`_stream_inner` 字节计数处，`:390-396` 附近）

- 给 `_download_from_url` / `_stream_inner` 注入一个 `progress_callback(downloaded_bytes, total_bytes)`（`total_bytes` 取自 `Content-Length`）。
- 把该回调串到下载 job 的逐篇更新里，`_progress_notify` 快照里带上 `downloaded_bytes`/`total_bytes`。
- 前端对单篇显示真实 `downloaded/total` 百分比，替代当前「下载中…」占位。

> 此步为增强项，可在 4.1–4.3 落地后单独做；不建议第一步就做（涉及下载函数签名贯穿，面更广）。

### 4.5 SSE 加固（已完成 ✅）

`engine/jobs.py::_progress_notify` 已改为「队列满时合并保留最新快照」，终态不丢帧。可选进一步把 `maxsize=64` 提到 256（内存占用很小，收益是慢客户端少触发一次合并）。

### 4.6 共享 JS 模块抽取（重构整洁性）

**文件**：`ui/html_templates.py`

- 当前 `PAPER_SELECTION_WIDGET_HTML` 和另一个渲染函数各自内嵌了一份 progress-stream JS。抽成一个共享的 `PROGRESS_STREAM_JS` 常量，两处引用，消除「SSE 逻辑改一处漏一处」的隐患。

---

## 五、涉及文件清单

| 文件 | 改动 | 风险 |
|---|---|---|
| `tools/orchestration.py` | 新增下载 job 入口 + 下载循环注入逐篇回调 | 中（核心逻辑） |
| `engine/jobs.py` | 基本复用；可选 maxsize 64→256 | 低 |
| `server.py` | POST 处理器异步化、返回 job_id | 中 |
| `ui/html_templates.py` | 删假计时器、接 SSE、抽共享 JS | 中 |
| `engine/download.py` | 可选字节级进度回调 | 中（可选） |
| `tools/core.py` | 若抽通用 job 查询端点需微调 | 低 |

---

## 六、分阶段实施

- **Phase 1（后端 job 化）**：`orchestration.py` 下载循环 + 复用 `jobs.py` 基建，先让 `submit_download_job` 能跑通、进度能被 `_progress_notify` 发布。**此时 MCP 工具调用方（agent）已可查询下载进度。**
- **Phase 2（HTTP 异步）**：`server.py` POST 改异步返回 job_id，浏览器端不再阻塞。
- **Phase 3（前端接 SSE）**：`html_templates.py` 删假计时器、接 SSE + 轮询兜底。
- **Phase 4（可选字节级）**：`download.py` 进度回调 + 前端单篇百分比。
- **Phase 5（兼容性收尾）**：确保 MCP 工具 `download_selected_papers` 的返回结构兼容旧调用方（返回 job_id + 指引轮询 `get_download_job_status`，或保留同步模式开关）。

---

## 七、风险与兼容性

1. **返回结构变更**：下载 job 化后，`download_selected_papers` 从「同步返回每篇结果」变为「返回 job_id + 异步进度」。需为 MCP/agent 调用方提供 `get_download_job_status`（或复用 `get_parse_job_status`），否则 agent 拿不到结果。
   - 缓解：保留一个 `sync` 模式开关（`parse_execution="sync"` 语义，下载也沿用），同步模式仍返回旧结构；异步模式才返回 job_id。
2. **daemon 线程与事件循环**：下载 job 的 daemon 线程要能安全地 `asyncio`（参考 `submit_parse_job` 的线程+loop 处理），避免「线程内跑 async」的坑。
3. **>10 篇 selection 确认**：下载 job 入口必须复用 `_should_require_large_batch_selection`，不能因为 job 化而绕过选择门槛。
4. **终端状态丢失**：已通过 SSE 合并（4.5）缓解；job 表里必须持久化终态，前端重连时能拉到最终快照。

---

## 八、验收标准

- [ ] 浏览器勾选 20 篇下载，进度条**实时**显示 `N/20`，每篇状态从 `downloading` → `downloaded`/`download_failed` 滚动更新。
- [ ] 下载失败（如付费墙）时，该篇明确标 `download_failed` 而非一直转圈。
- [ ] MCP 工具调用方（agent）能通过 `get_download_job_status` 查询到同样的逐篇进度。
- [ ] （若做 4.4）单篇大 PDF 显示真实字节百分比。
- [ ] >10 篇仍强制走 selection 确认，未因 job 化被绕过。

---

## 附：关联审计结论（同期 workflow 产出）

- **等待时间**：最大隐形延迟是下载 race 前**串行的 DOI→arXiv 元数据查询**（已并行化 + 6s 超时）。其余 env 超时已下调。
- **搜索源范围**：`pdf-cs` 配置档只映射 `arxiv/openalex/crossref`（dblp/semantic 未进），PDF-first 打分给 arXiv +2.0/+3.0、给付费 DOI 源 -5.0 并排除。若要检索覆盖付费 DOI，需改 `engine/search.py` 的 `PDF_CS_SOURCES` + `_download_route_for_candidate`（独立于本方案，另行决策）。
- **浏览器自动拉起**：`open_browser=true` 原为 no-op，已接入 `webbrowser.open()`。
