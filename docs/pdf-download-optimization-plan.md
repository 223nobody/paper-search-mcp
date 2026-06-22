# PDF 下载能力优化方案

> 基于对 [scansci-pdf](https://github.com/Rimagination/scansci-pdf) 的架构分析和当前 paper-search-mcp 下载引擎的全面审计。
>
> 日期：2026-06-22

---

## 一、问题诊断：为什么当前只有 arXiv 能稳定下载

下载链路核心在 [download.py](../paper_search_mcp/engine/download.py) 的 `_download_with_fallback_path`，它按 `primary → repositories → unpaywall → publisher_direct → paper_fetch → libgen → sci-hub` 的顺序尝试。问题出在**每一层都存在被阻断的根因**：

| 层级 | 当前实现 | 失效原因 |
|------|---------|---------|
| **primary** (源原生下载) | 调用 `searcher.download_pdf()` | semantic/pmc/europepmc 返回 OA URL 后使用 `httpx` 下载 → 被 TLS 指纹检测拦截 (403) |
| **repositories** (仓库回退) | 用 DOI/标题搜索仓库后 `_download_from_url` | 同上，httpx 被 Cloudflare/Publisher 防火墙拦截 |
| **unpaywall** | `UnpaywallResolver` 解析 OA URL → `_download_from_url` | OA URL 可能是 publisher 托管的，httpx 被拦截 |
| **publisher_direct** | 仅 11 个 DOI 前缀模板 | 覆盖面窄，且多数 publisher（Elsevier/Springer/Nature）即使拼出 PDF URL 也会返回 403（需 cookie/sso + TLS 指纹） |
| **paper_fetch** | 默认关闭 | 依赖外部包，启用门槛高 |
| **libgen** | 默认关闭，需显式 `use_libgen=true` | requests + bs4 抓取，无代理轮换 |
| **sci-hub** | 默认关闭，需显式 `use_scihub=true` | 基础 `requests.Session`，无 Tor 代理、无 captcha 处理、无反检测 |

**核心结论**：fallback 链虽然设计精巧（race/sequential 策略、健康度排名），但**每一层的 HTTP 客户端都是裸 `httpx`/`requests`**，而现代学术出版商（Elsevier、Springer、Wiley、IEEE、ACS 等）普遍部署了 Cloudflare Bot Management + TLS 指纹检测，导致即使拼出正确的 PDF URL 也会被 403 拦截。scansci-pdf 正是针对这个根本问题做了一套完整方案。

---

## 二、scansci-pdf 的关键技术优势（应该借鉴的）

### 2.1 反检测浏览器引擎 (CloakBrowser)

- 基于 Playwright 的反检测浏览器，能通过 Cloudflare Turnstile
- **关键发现**：出版社（PNAS、Elsevier 等）会检测 **TLS 指纹**，Python HTTP 客户端即使带有效 cookies 也会返回 403
- PDF **必须通过浏览器下载**才能绕过 TLS 指纹检测

### 2.2 Tor + FlareSolverr 双层代理

- 嵌入式 Tor Expert Bundle（无需 Docker）
- FlareSolverr 专门处理 Cloudflare 5 秒盾/challenge 页面
- 当裸 HTTP 请求被拦截时，自动升级到代理链路

### 2.3 机构代理 (WebVPN / CARSI / EZProxy)

- 支持 100+ 中国高校 WebVPN
- CAS/CARSI 联邦认证 + OpenAthens + Shibboleth SSO
- 登录后 cookies 持久化，同出版商论文无需重复认证

### 2.4 出版商直链 + SSO 登录流

- 覆盖 15+ 出版商：elsevier, wiley, springer, nature, science, ieee, tandfonline, pnas, acs, rsc, aip, aps, iop, oxford, acm
- 智能登录流：下载返回 paywall 错误 → 识别出版商 → 打开浏览器 → 用户完成机构 SSO → cookies 持久化
- Elsevier API 快速通道（免费 API key）

### 2.5 多源并行下载

- 13+ 源并发（publisher direct + Unpaywall + OpenAlex + Semantic Scholar + Crossref + DOAJ + EuropePMC + CORE + PMC + LibGen + Sci-Hub + ...）
- 默认 10 线程并发，支持断点续传

---

## 三、完整优化方案

### Phase 1：HTTP 层反检测能力（基础 — 投入产出比最高）

#### 1.1 为 `_download_from_url` 添加 TLS 指纹伪装

当前 [download.py](../paper_search_mcp/engine/download.py#L237-L299) 使用裸 `httpx.AsyncClient`，需要升级：

- **方案 A**（推荐，轻量）：集成 `curl_cffi` / `tls_client` — 模拟 Chrome TLS 指纹（JA3/JA4），绕过多数出版商的 TLS 检测
  - `pip install curl_cffi`
  - 替换 `httpx.AsyncClient` 为 `curl_cffi.requests.AsyncSession`，设置 `impersonate="chrome124"`
  - 改动范围：仅 `_download_from_url` 一个函数
  - 预期效果：能将 OA PDF 下载成功率从 ~20% 提升到 ~60%

- **方案 B**（备选）：集成 `playwright` / `nodriver` 进行浏览器级下载
  - 当 curl_cffi 也失败时（HTTP 403/406），fallback 到 headless browser 下载
  - 改动范围：新增一个 `_browser_download_from_url` fallback 函数

#### 1.2 添加 Cloudflare 绕过层

- 集成 **FlareSolverr**（Docker 或本地进程）：当 HTTP 返回 503 + "Checking your browser" 时，将请求路由到 FlareSolverr
- 新增 env 配置：`FLARESOLVERR_URL`（默认 `http://localhost:8191`）
- 改动范围：`_download_from_url` 的错误处理分支 + 新增 `_flaresolverr_download` 函数

#### 1.3 添加 Tor SOCKS5 代理支持

- 为下载请求添加可选的 SOCKS5 代理（Tor）
- 新增 env 配置：`DOWNLOAD_SOCKS5_PROXY`（如 `socks5://127.0.0.1:9050`）
- 用于 Sci-Hub / LibGen 下载时的 IP 轮换和匿名化
- 改动范围：httpx client 初始化时注入 `proxy` 参数

---

### Phase 2：出版商直链扩展（中等投入）

#### 2.1 扩展 publisher_direct 模板覆盖

当前 [publisher_direct.py](../paper_search_mcp/academic_platforms/publisher_direct.py) 只有 11 个 DOI 前缀模板，需扩展：

```python
# 新增模板（至少 20+ 个）
PUBLISHER_PDF_TEMPLATES 新增:
    "10.1016": elsevier_pdf_url,     # Elsevier/ScienceDirect
    "10.1002": wiley_pdf_url,         # Wiley
    "10.1007": springer_pdf_url,      # Springer (非 OA 也尝试)
    "10.1109": ieee_pdf_url,          # IEEE
    "10.1145": acm_pdf_url,           # ACM
    "10.1021": acs_pdf_url,           # ACS
    "10.1039": rsc_pdf_url,           # RSC
    "10.1063": aip_pdf_url,           # AIP
    "10.1103": aps_pdf_url,           # APS
    "10.1088": iop_pdf_url,           # IOP
    "10.1093": oxford_pdf_url,        # Oxford Academic
    "10.1073": pnas_pdf_url,          # PNAS
    "10.1126": science_pdf_url,       # Science
    "10.1080": tandfonline_pdf_url,   # Taylor & Francis
    "10.1001": jamanetwork_pdf_url,   # JAMA
    "10.1056": nejm_pdf_url,          # NEJM
    "10.1136": bmj_pdf_url,           # BMJ
    "10.1017": cambridge_pdf_url,     # Cambridge
    "10.1515": degruyter_pdf_url,     # De Gruyter
    "10.1162": mitpress_pdf_url,      # MIT Press
```

#### 2.2 Publisher Direct 添加 cookie/session 支持

当前 `resolve_publisher_direct_url` 只返回裸 URL。需要新增：

- **Cookie 持久化**：`~/.paper_search_mcp/publisher_cookies.json`
- **登录流**：当 publisher PDF URL 返回 403/login 页面时，返回结构化错误 `{"error": "paywall", "action": "login_required", "publisher": "elsevier"}`，让上层触发浏览器 SSO 登录
- **Elsevier API 集成**：利用免费 Elsevier API key 获取 ScienceDirect PDF（`https://api.elsevier.com/content/article/doi/{doi}`）

---

### Phase 3：浏览器辅助下载（较大投入）

#### 3.1 新增浏览器下载引擎 `browser_download.py`

```
paper_search_mcp/engine/browser_download.py
```

核心功能：

- 基于 **Playwright** 的 headful/headless 浏览器下载
- 支持 Chromium 的反检测 patch（去除 `navigator.webdriver` 等自动化标记）
- 用于处理以下场景：
  1. HTTP 客户端被 TLS 指纹拦截 → 升级到浏览器下载
  2. Publisher 要求 JavaScript 渲染（SPA 论文页面）
  3. 需要完成 SSO 登录流
  4. Cloudflare Turnstile challenge

#### 3.2 新增机构登录模块 `institutional_login.py`

```
paper_search_mcp/engine/institutional_login.py
```

核心功能：

- WebVPN / CARSI / EZProxy 三种登录模式
- 浏览器打开 publisher 页面 → 用户点击 "Access through your institution" → 选择机构 → 完成 SSO
- Cookies 持久化到 `publisher_cookies.json`，按 publisher 域名分组
- 后续同一 publisher 的论文自动使用已保存的 cookies

新增 MCP 工具：

- `login_publisher(identifier)` — 对某篇论文的出版商进行登录
- `list_publisher_sessions()` — 查看已登录的出版商
- `clear_publisher_sessions()` — 清除登录状态

> **注**：这个功能的主要受众是国内高校用户（CARSI 联盟），如果目标用户不限于国内，可以优先考虑 OpenAthens/Shibboleth 通用方案。

---

### Phase 4：Sci-Hub / LibGen 增强（较小投入）

#### 4.1 Sci-Hub 增强

当前 [sci_hub.py](../paper_search_mcp/academic_platforms/sci_hub.py) 比较原始，需增强：

- 添加 **多域名轮换**：`.se`, `.st`, `.ru`, `.ee`，自动检测可用域名
- 添加 **Tor SOCKS5 代理**：避免 IP 被封
- 添加 **captcha 检测与处理**：识别 Sci-Hub 的 captcha 页面，提示用户手动输入
- 添加 **DOI redirect 预检**：先访问 `https://doi.org/{doi}` 获取最终 publisher URL，再提交给 Sci-Hub（提高匹配率）

#### 4.2 LibGen 增强

当前 [libgen.py](../paper_search_mcp/academic_platforms/libgen.py) 已经不错，可增强：

- 添加 **Fiction/SCITECH 双库搜索**：当前只搜 JSON API，可同时搜 HTML 界面
- 添加 **IPFS 网关 fallback**：`ipfs.io`, `cloudflare-ipfs.com`, `dweb.link`
- 添加 **多 mirror 并发搜索**：当前是顺序试，可改为前 3 个 mirror 并发，取第一个成功

#### 4.3 可选：添加 Anna's Archive / Z-Library 源

- Anna's Archive 聚合了 LibGen + Sci-Hub + Z-Library + Internet Archive
- 可作为 LibGen 失败后的额外 fallback

---

### Phase 5：架构优化（可选，长期）

#### 5.1 下载策略扩展

当前 `DOWNLOAD_STRATEGY` 支持 `race / oa_first / sequential`，可新增：

- `browser_first`：优先用浏览器下载（适合有 publisher 登录的场景）
- `tor_only`：所有请求走 Tor（适合 Sci-Hub/LibGen 密集使用场景）

#### 5.2 健康度排名增强

当前 `rank_download_methods` 是全局排名，可改为：

- **per-publisher 排名**：Elsevier 的 primary 可能总失败，但 arXiv 的 primary 总是成功，应分开统计
- **自适应策略切换**：检测到连续 3 次 `403/406` 时自动升级到浏览器下载

#### 5.3 下载缓存（HTTP 缓存）

- 添加 ETag / If-Modified-Since 支持
- 缓存成功下载的 PDF URL → 实际 PDF 映射（避免重复下载同一 DOI）

---

## 四、优先级排序

| 优先级 | Phase | 改动量 | 预期效果 | 风险 |
|--------|-------|--------|---------|------|
| **P0（立即）** | Phase 1.1: TLS 指纹伪装 (curl_cffi) | 小（单文件改动） | OA PDF 成功率 20%→60% | 低 |
| **P0（立即）** | Phase 2.1: 扩展 publisher_direct 模板 | 小（单文件新增） | 覆盖主流出版商 DOI | 低 |
| **P1（本周）** | Phase 1.2: FlareSolverr 集成 | 中（新增模块） | 绕过 Cloudflare 拦截 | 中（需 Docker） |
| **P1（本周）** | Phase 1.3: Tor SOCKS5 | 小（配置+注入） | Sci-Hub/LibGen 更稳定 | 低 |
| **P2（本月）** | Phase 2.2: Publisher cookie/登录流 | 中（新增模块） | 付费墙论文可下载 | 中 |
| **P2（本月）** | Phase 4: Sci-Hub/LibGen 增强 | 小（修改现有） | 灰色渠道更可靠 | 低 |
| **P3（长期）** | Phase 3: 浏览器引擎 + 机构登录 | 大（全新子系统） | 完整反检测 + SSO | 高 |
| **P3（长期）** | Phase 5: 架构优化 | 中 | 长期可维护性 | 中 |

---

## 五、具体实施路线图

### 第一周（P0 项）

1. `pip install curl_cffi`，修改 `_download_from_url` 使用 curl_cffi 替代 httpx
2. 在 `publisher_direct.py` 新增 15-20 个出版商 PDF URL 模板
3. 测试 OA 论文下载成功率

### 第二周（P1 项）

4. 添加 FlareSolverr 集成（作为 HTTP 403/503 的 fallback）
5. 为 Sci-Hub/LibGen 请求添加 Tor SOCKS5 代理支持
6. 测试端到端下载链路

### 第三-四周（P2 项）

7. 实现 publisher cookie 持久化
8. 增强 Sci-Hub（多域名轮换、captcha 检测）
9. 增强 LibGen（IPFS fallback、并发搜索）

### 长期（P3 项）

10. 评估是否需要完整的 Playwright 浏览器引擎（取决于 P0-P2 的效果）
11. 如果需要，逐步实现机构登录和浏览器下载

---

## 六、风险评估与注意事项

1. **curl_cffi 兼容性**：curl_cffi 对 Python 版本有要求（≥3.8），Windows 上可能有编译问题，需要测试
2. **FlareSolverr 依赖**：需要 Docker 或独立进程，增加部署复杂度。可改为可选的 fallback（未配置则跳过）
3. **Sci-Hub 法律风险**：保持 opt-in 模式（`use_scihub=true`），不默认启用
4. **浏览器自动化维护成本**：Playwright 依赖 Chromium 二进制，版本更新频繁。建议放在 P3，先看 curl_cffi 的效果
5. **机构登录受众面**：CARSI/WebVPN 主要服务国内用户，如果 MCP 面向国际用户，应该同时支持 OpenAthens/Shibboleth

---

## 七、总结

最小可行改进是 **P0 的 curl_cffi + 扩展 publisher_direct 模板**，这两个改动加起来不到 200 行代码，但能把 PDF 下载能力从"只能下 arXiv"提升到"大部分 OA 论文都能下"。后续的 Tor/FlareSolverr/浏览器层则可以按需推进。
