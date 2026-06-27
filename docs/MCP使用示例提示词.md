# MCP 使用示例提示词

> 本文档提供 paper-search-mcp 的各场景最佳提示词模板，已针对 MCP 功能设计进行了优化。
> 将提示词直接粘贴到 Claude Code / Codex / VS Code Copilot 等 MCP Host 中使用。

---

## 场景速查

| 场景 | 推荐工具 | 关键参数 |
|------|---------|---------|
| [一站式全流程](#场景-1一站式全流程最常用) | `paper_research_workflow` | `ranking_profile='agent-skill'` |
| [只检索不下载](#场景-2只检索不下载) | `paper_research_workflow` + `intent='search_only'` | `sources='arxiv,semantic'` |
| [手动选择后下载解析](#场景-3手动选择论文) | `paper_research_workflow` + `selection_mode='manual'` | `count=10` |
| [已知论文直接下载](#场景-4已知论文直接下载) | `download_arxiv` / `download_with_fallback` | `paper_id` |
| [出版商正式版本](#场景-5获取出版商正式版) | `download_publisher_version` | `paper_key` |
| [IEEE/ACM 专属搜索](#场景-6-ieeeacm-专属搜索) | `paper_research_workflow` | `sources='ieee,acm,arxiv'` |
| [解析已下载 PDF](#场景-7解析已有-pdf) | `parse_pdf_with_mineru` | `mode='auto'` |
| [搜索已解析论文](#场景-8搜索已解析论文) | `search_parsed_papers` | `query` |
| [环境诊断](#场景-9环境诊断) | `check_publisher_setup` / `mineru_health_check` | — |
| [后台任务管理](#场景-10后台解析任务管理) | `get_parse_job_status` / `list_parse_jobs` | `job_id` |

---

## 场景 1：一站式全流程（最常用）

**说明**：搜索 → 下载 PDF → 后台 MinerU 解析，一句话完成。适用于大多数 CS/AI 论文检索场景。

```text
用 paper-search-mcp 的 paper_research_workflow，
搜索 5 篇关于 "LLM agent skill library" 的论文，
使用 ranking_profile='agent-skill'，
下载 PDF 并后台解析，parse_execution='background'。
```

**关键参数说明**：
- `ranking_profile='agent-skill'`：触发 agent-skill 评分策略，提高 LLM Agent/Skill 领域论文的排序精度
- `parse_execution='background'`：后台解析，返回 `job_id` 后用 `get_parse_job_status` 查询进度
- `parse_execution='sync'`：同步等待解析完成（适合少量论文）
- `parse_execution='none'`：只下载不解析

---

## 场景 2：只检索不下载

**说明**：先浏览搜索结果，确认论文质量后再决定是否下载。

```text
用 paper-search-mcp 搜索 "multi-agent reinforcement learning" 论文，
从 arxiv 和 semantic 各找 5 篇，
intent='search_only'，先不下载。
```

**变体**：
```text
# 全面检索 + 年份过滤（对所有源生效）
用 paper-search-mcp 搜索 "federated learning privacy"，
使用 sources='deep'，year='2024'，先看看结果。
```

> 💡 `year` 参数通过**后置过滤**实现，对所有搜索源（arxiv/crossref/openalex 等）均生效，不依赖单源 API 的年份支持。

---

## 场景 3：手动选择论文

**说明**：搜索结果超过 10 篇或想手动筛选时使用。

```text
用 paper-search-mcp 搜索 "retrieval augmented generation" 论文 15 篇，
使用 sources='arxiv,semantic,crossref'，
selection_mode='manual'，让我挑选后再下载解析。
```

**变体**：
```text
# 先编号列表，再用编号选择
用 paper-search-mcp 搜索 "chain of thought prompting" 论文，
返回编号列表，我选第 1、3、5 篇下载解析。
```

---

## 场景 4：已知论文直接下载

**说明**：已知 arXiv ID、DOI 或论文标题时，直接下载。

```text
# 按 arXiv ID
用 paper-search-mcp 下载 arXiv 论文 2301.12345。

# 按 DOI
用 paper-search-mcp 下载论文 10.1038/s41586-023-06967-4，
使用 download_with_fallback。

# 按标题（走开放获取优先回退链）
用 paper-search-mcp 下载论文 "Attention Is All You Need"，
优先走开放获取渠道。
```

---

## 场景 5：获取出版商正式版

**说明**：已有 arXiv 预印本缓存，想获取 Nature/Elsevier/Springer 等正式排版版本。

```text
# 单篇
用 paper-search-mcp 下载 paper_key=arxiv_1706.03762 的出版商最终发行版。

# 先搜索 → 下载 arXiv → 升级出版商版
用 paper-search-mcp：
1. 搜索 "Chain-of-Thought Prompting"，下载并解析 arXiv 版本
2. 用 download_publisher_version 获取出版商正式版

# 批量
用 paper-search-mcp 批量下载这些 arXiv 论文的出版商版本：
arxiv_2301.12345, arxiv_2302.23456, arxiv_2303.34567

# 下载并重新解析
用 paper-search-mcp 下载 arxiv_1810.04805 的出版商版本，force_reparse=True。
```

**前提**：
- 论文需已在 paper-search-mcp 缓存中（先下载 arXiv 版本即自动缓存）。
- **单篇工具**：缓存未命中时自动下载 arXiv 预印本并继续查找出版商版本。
- **批量工具**：同样具有自动下载回退能力，无需手动预缓存所有论文。
- 首次使用自动安装 scansci-pdf。

> ⚠️ 批量下载建议每次不超过 10 篇，超时设置 `timeout=300`。

---

## 场景 6：IEEE/ACM 专属搜索

**说明**：需要 Key 已配置（`PAPER_SEARCH_MCP_IEEE_API_KEY` / `PAPER_SEARCH_MCP_ACM_API_KEY`）。

```text
# IEEE 搜索
用 paper-search-mcp 搜索 IEEE 中关于 "hardware security" 的 3 篇论文，
使用 sources='ieee,arxiv'，下载并解析。

# ACM 搜索
用 paper-search-mcp 搜索 ACM 中关于 "program synthesis" 的论文 5 篇，
使用 sources='acm,arxiv,crossref'，下载并后台解析。
```

**注意**：IEEE/ACM 搜索结果会自动提取 DOI 并路由到 scansci-pdf 出版商下载。

---

## 场景 7：解析已有 PDF

**说明**：已下载 PDF 但未解析，或想重新解析。

```text
# 单篇
用 paper-search-mcp 解析 ~/Desktop/paper.pdf，mode='auto'。

# 多篇
用 paper-search-mcp 批量解析以下 PDF：
~/Desktop/papers/paper1.pdf
~/Desktop/papers/paper2.pdf
~/Desktop/papers/paper3.pdf
使用 mode='extract'。
```

**模式说明**：
- `auto`：优先 MinerU 官方 API，逐级回退到本地/pypdf
- `extract`：强制使用 MinerU 官方 extract API（高质量版面分析）
- `cli`：使用本地 MinerU CLI
- `pypdf`：基础文本提取（无版面分析），速度快、无需 API Key

> 💡 **路径提示**：如果 `parse_pdf_with_mineru` 报 "PDF not found"，可能原因有：(1) 下载和解析使用了不同的 `save_path`；(2) Windows 上 `~/Desktop/` 解析不一致。建议使用下载结果中返回的 `pdf_path` 字段，而非自行构造路径。系统也会自动尝试通过缓存元数据查找 PDF 的替代路径。

---

## 场景 8：搜索已解析论文

**说明**：利用 FTS 全文本索引在已解析的论文缓存中搜索。

> ⚠️ **前提**：论文需先被 MinerU/pypdf 解析后才会自动加入 FTS 索引。新解析的论文**自动索引**，旧论文重新解析后也会自动索引。如果搜索返回空结果，先运行 `index_parsed_cache` 重建索引。

```text
# 全缓存搜索
用 paper-search-mcp 在我已解析的所有论文中搜索 "attention mechanism"。

# 单篇搜索
用 paper-search-mcp 在 paper_key=my_paper 中搜索 "experiment setup"。

# 先重建索引再搜索（解决搜索返回空的问题）
用 paper-search-mcp 重建解析缓存 FTS 索引，
然后搜索 "contrastive learning"。
```

---

## 场景 9：环境诊断

**说明**：首次使用或遇到问题时诊断环境。

```text
# 全量诊断
用 paper-search-mcp：
1. diagnose_paper_sources 查看各来源配置
2. check_publisher_setup 检查出版商下载环境
3. mineru_health_check 检查 MinerU 解析环境

# 单独检查
用 paper-search-mcp 检查 MinerU API Key 是否已配置。
用 paper-search-mcp 查看下载健康统计，哪些渠道成功率最高。
```

---

## 场景 10：后台解析任务管理

**说明**：提交后台解析后，查询进度、取消或恢复任务。

```text
# 查询进度
用 paper-search-mcp 查询解析任务 job_xxx 的状态。

# 列出所有任务
用 paper-search-mcp 列出当前所有后台解析任务。

# 取消任务
用 paper-search-mcp 取消解析任务 job_xxx。

# 恢复中断的任务
用 paper-search-mcp 恢复解析任务 job_xxx，force=False。
```

---

## 场景 11：缓存维护

**说明**：定期维护解析缓存，解决 FTS 搜索返回空、缓存失效等问题。

```text
# 失效条目清理（先 dry-run）
用 paper-search-mcp 清理失效的解析缓存条目，先 dry-run 预览。

# 确认清理
确认清理那些 PDF 已失效的缓存条目，apply=True。

# 重建 FTS 索引（搜索返回空结果时使用）
用 paper-search-mcp 重建解析缓存的 FTS 索引。

# 删除单条缓存
用 paper-search-mcp 删除 paper_key=example 的解析缓存。

# 冗余产物清理
用 paper-search-mcp 清理冗余的缓存产物，先 dry-run。
```

> 💡 **常见问题排查**：如果 `search_parsed_papers` 返回空结果但 `list_parsed_papers` 显示有已解析论文，运行 `index_parsed_cache` 重建 FTS 索引即可修复。新解析的论文会自动索引，无需手动操作。

---

## 参数速查

### `paper_research_workflow` 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|:--:|------|
| `query` | str | *必填* | 搜索关键词 |
| `intent` | str | `search_download_parse` | `search_only` / `search_download` / `search_download_parse` |
| `count` | int | 5 | 需要的论文数量 |
| `sources` | str | `""` | 源列表，如 `"arxiv,semantic,crossref"`；空则用 SEARCH_PROFILE |
| `ranking_profile` | str | `""` | `"agent-skill"` 触发 LLM Agent 领域排序策略 |
| `selection_mode` | str | `auto_top` | `auto_top` 自动选前 N 篇 / `manual` 手动选择 |
| `parse_execution` | str | `none` | `none` / `background` / `sync` / `prompt` |
| `year` | str | `None` | 年份后置过滤（全源生效），如 `"2024"` |

### `download_publisher_version` 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|:--:|------|
| `paper_key` | str | *必填* | 缓存中的论文 key，如 `"arxiv_1706.03762"` |
| `timeout` | int | 120 | 单篇超时秒数 |
| `force_reparse` | bool | False | 下载后是否自动用 MinerU 重新解析 |

### `batch_download_publisher_versions` 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|:--:|------|
| `paper_keys` | str | *必填* | 逗号分隔的 paper key 列表，或 `"all"` 处理全部 |
| `save_path` | str | `~/Desktop/papers` | 出版商 PDF 保存目录 |
| `timeout` | int | 300 | 整批超时秒数 |
| `force_reparse` | bool | False | 下载后是否自动解析 |

### `check_publisher_setup` 返回内容

- `scansci_pdf_installed` — scansci-pdf 是否已安装
- `components` — CloakBrowser / Tor / Crypto 可用性
- `playwright` — Chromium 浏览器状态
- `source_scores` — 各下载源 EMA 评分
- `source_health_advice` — 分级建议（preferred / healthy / degraded / avoid）
- `scihub` — 7 个 Sci-Hub 域名可达性诊断

---

## 搜索源速查

### 源配置文件

| Profile | 包含源 | 适用场景 |
|---------|------|------|
| `pdf-cs`（默认） | arxiv, openalex, crossref, dblp | CS 论文，PDF 优先 |
| `agent-skill-fast` | arxiv, openalex, crossref | Agent/Skill 领域快速检索 |
| `agent-skill-broad` | +semantic, +google_scholar | Agent/Skill 领域扩展检索 |
| `fast` | arxiv, pubmed, biorxiv, medrxiv, semantic, crossref, openalex, pmc, core, europepmc, dblp | 通用快速检索 |
| `deep` / `all` | 全部 21 源 | 全面检索（慢） |

### 可按 PDF 下载能力分组的源

| 能力 | 源 |
|------|-----|
| **可直接下载 PDF** | arxiv, pmc, biorxiv, medrxiv, iacr |
| **通过 scansci-pdf 下载** | 有 DOI 的论文，支持 Elsevier/Springer/Nature 等出版商 |
| **仅元数据** | crossref, openalex, dblp, google_scholar, unpaywall |
| **下载不稳定** | semantic, core, citeseerx, doaj, base, zenodo, hal, openaire |
| **需 Key** | ieee (IEEE_API_KEY), acm (ACM_API_KEY) |
