# paper_search_mcp/server.py
from typing import List, Dict, Optional, Any
import asyncio
import copy
import json
import os
import logging
import re
import secrets
import threading
import time
import webbrowser
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from pathlib import Path
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, create_model
from .config import env_file_path, get_env, set_env_value
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.pmc import PMCSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.europepmc import EuropePMCSearcher
from .academic_platforms.sci_hub import SciHubFetcher
from .academic_platforms.dblp import DBLPSearcher
from .academic_platforms.openaire import OpenAiresearcher
from .academic_platforms.citeseerx import CiteSeerXSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.base_search import BASESearcher
from .academic_platforms.unpaywall import UnpaywallResolver, UnpaywallSearcher
from .academic_platforms.zenodo import ZenodoSearcher
from .academic_platforms.hal import HALSearcher
from .academic_platforms.ssrn import SSRNSearcher
from .utils import DEFAULT_SAVE_PATH, extract_doi, resolve_save_path
from .cache import (
    cleanup_redundant_artifacts,
    create_search_session as cache_create_search_session,
    delete_cache,
    delete_search_session as cache_delete_search_session,
    get_search_session as cache_get_search_session,
    list_assets,
    list_parsed,
    list_search_sessions as cache_list_search_sessions,
    read_parsed,
    record_download,
    resolved_parsed_paths,
    search_parsed,
)
from .parsers.mineru import (
    mineru_health_check as run_mineru_health_check,
    parse_pdf_with_mineru as run_parse_pdf_with_mineru,
)

# from .academic_platforms.hub import SciHubSearcher
from .paper import Paper

# Initialize MCP server
mcp = FastMCP("paper_search_server")
logger = logging.getLogger(__name__)
ALLOW_CUSTOM_SAVE_PATH_ENV = "ALLOW_CUSTOM_SAVE_PATH"
SEARCH_PROFILE_ENV = "SEARCH_PROFILE"
SEARCH_TIMEOUT_ENV = "SEARCH_TIMEOUT_SECONDS"
SEARCH_SOURCE_TIMEOUT_ENV = "SEARCH_SOURCE_TIMEOUT_SECONDS"
SEARCH_CACHE_TTL_ENV = "SEARCH_CACHE_TTL_SECONDS"
PARSE_CONCURRENCY_ENV = "PARSE_CONCURRENCY"
PAPER_SELECTION_WIDGET_URI = "ui://paper-search/paper-selection.html"
PAPER_SELECTION_WIDGET_TOOL = "render_paper_selection_app"
MINERU_KEY_WIDGET_URI = "ui://paper-search/mineru-api-key.html"
MINERU_KEY_WIDGET_TOOL = "render_mineru_api_key_setup_app"
MINERU_KEY_CONFIG_TOOL = "configure_mineru_api_key"
LOCAL_PAPER_SELECTION_TOOL = "open_paper_selection_page"
LOCAL_PAPER_SELECTION_PATH = "/paper-selection"
AUTO_PARSE_SAVED_PDF_LIMIT = 10
_LOCAL_SELECTION_LOCK = threading.Lock()
_LOCAL_SELECTION_SERVER: Optional[ThreadingHTTPServer] = None
_LOCAL_SELECTION_THREAD: Optional[threading.Thread] = None
_LOCAL_SELECTION_BASE_URL = ""
_LOCAL_SELECTION_PAGES: Dict[str, Dict[str, Any]] = {}
_SEARCH_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}


def _split_env_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = get_env(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _configure_http_transport_from_env() -> None:
    host = get_env("HOST", mcp.settings.host).strip() or mcp.settings.host
    port_raw = get_env("PORT", str(mcp.settings.port)).strip()
    try:
        port = int(port_raw)
    except ValueError:
        logger.warning("Invalid PAPER_SEARCH_MCP_PORT=%r; using %s", port_raw, mcp.settings.port)
        port = mcp.settings.port

    path = get_env("MCP_PATH", mcp.settings.streamable_http_path).strip() or mcp.settings.streamable_http_path
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path

    disable_security = _env_bool("DISABLE_DNS_REBINDING_PROTECTION", False)
    allowed_hosts = _split_env_csv(get_env("ALLOWED_HOSTS", ""))
    allowed_origins = _split_env_csv(get_env("ALLOWED_ORIGINS", ""))
    if disable_security:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=[],
            allowed_origins=[],
        )
        return

    if allowed_hosts or allowed_origins:
        existing = mcp.settings.transport_security or TransportSecuritySettings()
        mcp.settings.transport_security = existing.model_copy(
            update={
                "allowed_hosts": allowed_hosts or existing.allowed_hosts,
                "allowed_origins": allowed_origins or existing.allowed_origins,
            }
        )


def _local_selection_page_url(page_id: str) -> str:
    _ensure_local_selection_server()
    return f"{_LOCAL_SELECTION_BASE_URL}{LOCAL_PAPER_SELECTION_PATH}/{page_id}"


def _ensure_local_selection_server() -> None:
    global _LOCAL_SELECTION_BASE_URL, _LOCAL_SELECTION_SERVER, _LOCAL_SELECTION_THREAD
    with _LOCAL_SELECTION_LOCK:
        if _LOCAL_SELECTION_SERVER is not None:
            return

        host = get_env("LOCAL_UI_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port_raw = get_env("LOCAL_UI_PORT", "0").strip() or "0"
        try:
            port = int(port_raw)
        except ValueError:
            logger.warning("Invalid PAPER_SEARCH_MCP_LOCAL_UI_PORT=%r; using a random free port", port_raw)
            port = 0

        _LOCAL_SELECTION_SERVER = ThreadingHTTPServer((host, port), _LocalSelectionHandler)
        selected_host, selected_port = _LOCAL_SELECTION_SERVER.server_address[:2]
        if selected_host in {"0.0.0.0", ""}:
            selected_host = "127.0.0.1"
        _LOCAL_SELECTION_BASE_URL = f"http://{selected_host}:{selected_port}"
        _LOCAL_SELECTION_THREAD = threading.Thread(
            target=_LOCAL_SELECTION_SERVER.serve_forever,
            name="paper-search-local-selection-ui",
            daemon=True,
        )
        _LOCAL_SELECTION_THREAD.start()


def _create_local_selection_page(
    *,
    selection_token: str,
    papers: List[Dict[str, Any]],
    save_path: str,
    use_scihub: bool,
    mode: str,
    backend: str,
    force: bool,
) -> Dict[str, Any]:
    page_id = secrets.token_urlsafe(16)
    _LOCAL_SELECTION_PAGES[page_id] = {
        "selection_token": selection_token,
        "papers": papers,
        "save_path": save_path or DEFAULT_SAVE_PATH,
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
    }
    return {
        "page_id": page_id,
        "url": _local_selection_page_url(page_id),
    }


def _render_local_selection_html(page_id: str, page: Dict[str, Any]) -> str:
    papers = page.get("papers", [])
    rows = []
    for paper in papers if isinstance(papers, list) else []:
        index = paper.get("index")
        disabled = paper.get("parse_ready") is False or not isinstance(index, int)
        meta_bits = [
            str(paper.get("source") or ""),
            str(paper.get("year") or ""),
            str(paper.get("paper_id") or ""),
            str(paper.get("doi") or ""),
            str(paper.get("reason") or ""),
        ]
        meta = " | ".join(bit for bit in meta_bits if bit)
        rows.append(
            """
            <label class="paper{disabled_class}">
              <input type="checkbox" name="paper" value="{index}" {disabled}>
              <span>
                <span class="title">{index}. {title}</span>
                <span class="meta">{meta}</span>
              </span>
            </label>
            """.format(
                disabled_class=" disabled" if disabled else "",
                index=html_escape(str(index or "")),
                disabled="disabled" if disabled else "",
                title=html_escape(str(paper.get("title") or "Untitled")),
                meta=html_escape(meta),
            )
        )

    body = "\n".join(rows) if rows else '<div class="empty">No papers available.</div>'
    data_json = html_escape(
        json.dumps(
            {
                "page_id": page_id,
                "selection_token": page.get("selection_token", ""),
                "save_path": page.get("save_path", DEFAULT_SAVE_PATH),
                "use_scihub": bool(page.get("use_scihub")),
                "mode": page.get("mode", "auto"),
                "backend": page.get("backend", ""),
                "force": bool(page.get("force")),
            }
        ),
        quote=True,
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper selector</title>
  <style>
    :root {{ color-scheme: light dark; --bg: #eef3f7; --glass: rgba(255, 255, 255, .68); --glass-strong: rgba(255, 255, 255, .84); --text: #111827; --muted: #667085; --line: rgba(15, 23, 42, .13); --accent: #0f766e; --accent-strong: #0b5f59; --danger: #b42318; --disabled: rgba(148, 163, 184, .18); --shadow: 0 24px 70px rgba(15, 23, 42, .18); }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0d1117; --glass: rgba(22, 27, 34, .70); --glass-strong: rgba(31, 37, 46, .84); --text: #f8fafc; --muted: #aeb7c5; --line: rgba(226, 232, 240, .14); --accent: #2dd4bf; --accent-strong: #5eead4; --danger: #f97066; --disabled: rgba(71, 85, 105, .28); --shadow: 0 28px 80px rgba(0, 0, 0, .38); }} }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: linear-gradient(135deg, var(--bg), color-mix(in srgb, var(--bg), #dbeafe 28%)); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; line-height: 1.45; }}
    main {{ min-height: 100vh; padding: 18px; display: grid; place-items: start center; }}
    .shell {{ width: min(100%, 860px); margin: 0 auto; background: linear-gradient(180deg, var(--glass-strong), var(--glass)); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; box-shadow: var(--shadow); backdrop-filter: blur(22px) saturate(150%); -webkit-backdrop-filter: blur(22px) saturate(150%); }}
    header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 20px 15px; border-bottom: 1px solid var(--line); background: linear-gradient(180deg, rgba(255, 255, 255, .18), transparent); }}
    h1 {{ margin: 0; font-size: 18px; font-weight: 700; }}
    .token {{ max-width: 46%; color: var(--muted); font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; text-align: right; }}
    .list {{ display: grid; max-height: 58vh; overflow: auto; padding: 6px 0; scrollbar-width: thin; }}
    .paper {{ display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 10px; padding: 12px 20px; border-bottom: 1px solid var(--line); transition: background .16s ease, border-color .16s ease; }}
    .paper:hover {{ background: rgba(255, 255, 255, .22); }}
    .paper.disabled {{ background: var(--disabled); color: var(--muted); }}
    input[type="checkbox"] {{ width: 17px; height: 17px; margin-top: 2px; accent-color: var(--accent); }}
    .title {{ display: block; font-weight: 650; overflow-wrap: anywhere; }}
    .meta {{ display: block; color: var(--muted); font-size: 12px; margin-top: 3px; overflow-wrap: anywhere; }}
    .toolbar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 9px; padding: 14px 20px 16px; border-top: 1px solid var(--line); background: rgba(255, 255, 255, .12); }}
    button {{ min-height: 36px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .30); color: var(--text); padding: 8px 13px; font: inherit; font-weight: 600; cursor: pointer; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .35); transition: transform .14s ease, border-color .14s ease, background .14s ease; }}
    button:hover:not(:disabled) {{ transform: translateY(-1px); border-color: color-mix(in srgb, var(--accent), var(--line)); background: rgba(255, 255, 255, .44); }}
    button.primary {{ background: linear-gradient(180deg, var(--accent-strong), var(--accent)); border-color: transparent; color: #fff; box-shadow: 0 10px 24px rgba(15, 118, 110, .25); }}
    button:disabled {{ opacity: .58; cursor: not-allowed; }}
    .status {{ margin-left: auto; min-height: 20px; color: var(--muted); font-size: 12px; white-space: pre-wrap; }}
    .status.error {{ color: var(--danger); }}
    pre {{ margin: 0; padding: 14px 20px; border-top: 1px solid var(--line); overflow: auto; max-height: 280px; background: rgba(15, 23, 42, .06); color: var(--text); }}
    .empty {{ padding: 34px 20px; color: var(--muted); text-align: center; }}
    @media (max-width: 640px) {{ main {{ padding: 10px; }} header {{ display: grid; }} .token {{ max-width: none; text-align: left; }} .toolbar {{ align-items: stretch; }} .status {{ width: 100%; margin-left: 0; }} }}
  </style>
</head>
<body>
  <main>
    <section class="shell">
      <header>
        <h1>Paper selector</h1>
        <div class="token">{html_escape(str(page.get("selection_token", "")))}</div>
      </header>
      <form id="form" data-page="{data_json}">
        <div class="list">{body}</div>
        <div class="toolbar">
          <button class="primary" id="parse" type="submit">Parse selected</button>
          <button id="select-all" type="button">All</button>
          <button id="clear" type="button">Clear</button>
          <span class="status" id="status"></span>
        </div>
      </form>
      <pre id="result" hidden></pre>
    </section>
  </main>
  <script>
    const form = document.getElementById("form");
    const parseButton = document.getElementById("parse");
    const statusNode = document.getElementById("status");
    const resultNode = document.getElementById("result");
    const data = JSON.parse(form.dataset.page);

    function selectedIndices() {{
      return Array.from(document.querySelectorAll('input[name="paper"]:checked')).map((item) => item.value);
    }}

    function setStatus(message, kind = "") {{
      statusNode.textContent = message || "";
      statusNode.className = kind ? `status ${{kind}}` : "status";
    }}

    document.getElementById("select-all").addEventListener("click", () => {{
      document.querySelectorAll('input[name="paper"]:not(:disabled)').forEach((item) => item.checked = true);
    }});

    document.getElementById("clear").addEventListener("click", () => {{
      document.querySelectorAll('input[name="paper"]').forEach((item) => item.checked = false);
    }});

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const selected = selectedIndices();
      if (!selected.length) {{
        setStatus("Select at least one paper.", "error");
        return;
      }}
      parseButton.disabled = true;
      setStatus("Parsing...");
      resultNode.hidden = true;
      try {{
        const response = await fetch(`/api/parse-selection/${{encodeURIComponent(data.page_id)}}`, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ selected_indices: selected.join(",") }}),
        }});
        const body = await response.json();
        resultNode.textContent = JSON.stringify(body, null, 2);
        resultNode.hidden = false;
        setStatus(body.status ? `Done: ${{body.status}}` : "Done.");
      }} catch (error) {{
        setStatus(error?.message || String(error), "error");
      }} finally {{
        parseButton.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""


class _LocalSelectionHandler(BaseHTTPRequestHandler):
    server_version = "PaperSearchLocalSelection/1.0"

    def do_GET(self) -> None:
        page_id = self._page_id_from_path(LOCAL_PAPER_SELECTION_PATH)
        if not page_id:
            self._send_json({"status": "not_found"}, status=404)
            return
        page = _LOCAL_SELECTION_PAGES.get(page_id)
        if not page:
            self._send_json({"status": "not_found", "page_id": page_id}, status=404)
            return
        self._send_html(_render_local_selection_html(page_id, page))

    def do_POST(self) -> None:
        page_id = self._page_id_from_path("/api/parse-selection")
        if not page_id:
            self._send_json({"status": "not_found"}, status=404)
            return
        page = _LOCAL_SELECTION_PAGES.get(page_id)
        if not page:
            self._send_json({"status": "not_found", "page_id": page_id}, status=404)
            return
        try:
            payload = self._read_json()
            selected_indices = str(payload.get("selected_indices") or "")
            result = asyncio.run(
                parse_selected_papers(
                    selection_token=page["selection_token"],
                    selected_indices=selected_indices,
                    save_path=page.get("save_path", DEFAULT_SAVE_PATH),
                    use_scihub=bool(page.get("use_scihub")),
                    mode=page.get("mode", "auto"),
                    backend=page.get("backend", ""),
                    force=bool(page.get("force")),
                )
            )
            self._send_json(result)
        except Exception as exc:
            logger.exception("Local paper selection parse request failed")
            self._send_json({"status": "error", "message": str(exc)}, status=500)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("local selection ui: " + format, *args)

    def _page_id_from_path(self, prefix: str) -> str:
        path = self.path.split("?", 1)[0].rstrip("/")
        expected = prefix.rstrip("/") + "/"
        if not path.startswith(expected):
            return ""
        return path[len(expected) :]

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("content-length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

PAPER_SELECTION_WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #eef3f7;
      --glass: rgba(255, 255, 255, .68);
      --glass-strong: rgba(255, 255, 255, .84);
      --text: #111827;
      --muted: #667085;
      --line: rgba(15, 23, 42, .13);
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --danger: #b42318;
      --disabled: rgba(148, 163, 184, .18);
      --shadow: 0 24px 70px rgba(15, 23, 42, .18);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --glass: rgba(22, 27, 34, .70);
        --glass-strong: rgba(31, 37, 46, .84);
        --text: #f8fafc;
        --muted: #aeb7c5;
        --line: rgba(226, 232, 240, .14);
        --accent: #2dd4bf;
        --accent-strong: #5eead4;
        --danger: #f97066;
        --disabled: rgba(71, 85, 105, .28);
        --shadow: 0 28px 80px rgba(0, 0, 0, .38);
      }
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: linear-gradient(135deg, var(--bg), color-mix(in srgb, var(--bg), #dbeafe 28%));
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }

    main {
      min-height: 100vh;
      padding: 18px;
    }

    .shell {
      max-width: 820px;
      margin: 0 auto;
      background: linear-gradient(180deg, var(--glass-strong), var(--glass));
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px) saturate(150%);
      -webkit-backdrop-filter: blur(22px) saturate(150%);
    }

    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 20px 15px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255, 255, 255, .18), transparent);
    }

    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
    }

    .count {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    .list {
      display: grid;
      max-height: 460px;
      overflow: auto;
      padding: 6px 0;
      scrollbar-width: thin;
    }

    .paper {
      display: grid;
      grid-template-columns: 26px minmax(0, 1fr);
      gap: 10px;
      padding: 12px 20px;
      border-bottom: 1px solid var(--line);
      transition: background .16s ease, border-color .16s ease;
    }

    .paper:last-child { border-bottom: 0; }
    .paper:hover { background: rgba(255, 255, 255, .22); }
    .paper.disabled { background: var(--disabled); color: var(--muted); }

    input[type="checkbox"] {
      width: 17px;
      height: 17px;
      margin: 2px 0 0;
      accent-color: var(--accent);
    }

    .title {
      overflow-wrap: anywhere;
      font-weight: 600;
    }

    .meta {
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 9px;
      padding: 14px 20px 16px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .12);
    }

    button {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, .30);
      color: var(--text);
      padding: 8px 13px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .35);
      transition: transform .14s ease, border-color .14s ease, background .14s ease;
    }

    button.primary {
      border-color: transparent;
      background: linear-gradient(180deg, var(--accent-strong), var(--accent));
      color: #ffffff;
      font-weight: 650;
      box-shadow: 0 10px 24px rgba(15, 118, 110, .25);
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      border-color: color-mix(in srgb, var(--accent), var(--line));
      background: rgba(255, 255, 255, .44);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    .status {
      min-height: 20px;
      margin-left: auto;
      color: var(--muted);
      font-size: 12px;
      white-space: pre-wrap;
    }

    .status.error { color: var(--danger); }

    .empty {
      padding: 34px 20px;
      color: var(--muted);
      text-align: center;
    }

    @media (max-width: 640px) {
      main { padding: 10px; }
      header { display: grid; }
      .toolbar { align-items: stretch; }
      .status { width: 100%; margin-left: 0; }
    }
  </style>
</head>
<body>
  <main>
    <section class="shell" aria-labelledby="paper-selector-title">
      <header>
        <h1 id="paper-selector-title">Paper selector</h1>
        <div class="count" id="count"></div>
      </header>
      <form id="form">
        <div class="list" id="list"></div>
        <div class="toolbar">
          <button type="submit" class="primary" id="parse">Parse</button>
          <button type="button" id="select-all">All</button>
          <button type="button" id="clear">Clear</button>
          <div class="status" id="status"></div>
        </div>
      </form>
    </section>
  </main>
  <script>
    const list = document.getElementById("list");
    const count = document.getElementById("count");
    const form = document.getElementById("form");
    const parseButton = document.getElementById("parse");
    const selectAllButton = document.getElementById("select-all");
    const clearButton = document.getElementById("clear");
    const statusNode = document.getElementById("status");
    let rpcId = 1;
    const pending = new Map();
    let data = unwrapToolOutput(window.openai?.toolOutput || {});

    function unwrapToolOutput(value) {
      if (value?.result && typeof value.result === "object" && !Array.isArray(value.result)) {
        return value.result;
      }
      return value || {};
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;",
      })[ch]);
    }

    function setStatus(message, kind = "") {
      statusNode.textContent = message || "";
      statusNode.className = kind ? `status ${kind}` : "status";
    }

    function compactMeta(paper) {
      const bits = [];
      if (paper.source) bits.push(paper.source);
      if (paper.year) bits.push(paper.year);
      if (paper.paper_id) bits.push(paper.paper_id);
      if (paper.doi) bits.push(paper.doi);
      if (!paper.parse_ready && paper.reason) bits.push(paper.reason);
      return bits.join(" | ");
    }

    function render() {
      const papers = Array.isArray(data.papers) ? data.papers : [];
      const ready = papers.filter((paper) => paper.parse_ready !== false);
      count.textContent = `${ready.length}/${papers.length} ready`;
      parseButton.disabled = ready.length === 0;
      selectAllButton.disabled = ready.length === 0;
      clearButton.disabled = ready.length === 0;

      if (!papers.length) {
        list.innerHTML = '<div class="empty">No papers available.</div>';
        return;
      }

      list.innerHTML = papers.map((paper) => {
        const index = Number(paper.index);
        const disabled = paper.parse_ready === false || !Number.isFinite(index);
        return `
          <label class="paper${disabled ? " disabled" : ""}">
            <input type="checkbox" name="paper" value="${escapeHtml(index)}" ${disabled ? "disabled" : ""}>
            <span>
              <span class="title">${escapeHtml(index)}. ${escapeHtml(paper.title || "Untitled")}</span>
              <span class="meta">${escapeHtml(compactMeta(paper))}</span>
            </span>
          </label>
        `;
      }).join("");
    }

    function selectedIndices() {
      return Array.from(document.querySelectorAll('input[name="paper"]:checked')).map((item) => item.value);
    }

    function rpcRequest(method, params) {
      const id = rpcId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        window.setTimeout(() => {
          if (!pending.has(id)) return;
          pending.delete(id);
          reject(new Error("Timed out waiting for host response."));
        }, 120000);
      });
    }

    async function callTool(name, args) {
      if (window.openai?.callTool) {
        return window.openai.callTool(name, args);
      }
      return rpcRequest("tools/call", { name, arguments: args });
    }

    function structured(result) {
      return result?.structuredContent || result?.structured_content || result;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const selected = selectedIndices();
      if (!selected.length) {
        setStatus("Select at least one paper.", "error");
        return;
      }

      parseButton.disabled = true;
      setStatus("Parsing...");
      try {
        const result = await callTool("parse_selected_papers", {
          selection_token: data.selection_token || "",
          selected_indices: selected.join(","),
          save_path: data.save_path || "~/Desktop/papers",
          use_scihub: !!data.use_scihub,
          mode: data.mode || "auto",
          backend: data.backend || "",
          force: !!data.force,
        });
        const body = structured(result);
        const parsed = body?.parsed ?? 0;
        const failed = body?.failed ?? 0;
        const skipped = body?.skipped ?? 0;
        setStatus(`Done. parsed=${parsed}, failed=${failed}, skipped=${skipped}`);
      } catch (error) {
        setStatus(error?.message || String(error), "error");
      } finally {
        parseButton.disabled = false;
      }
    });

    selectAllButton.addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]:not(:disabled)').forEach((item) => {
        item.checked = true;
      });
      setStatus("");
    });

    clearButton.addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]').forEach((item) => {
        item.checked = false;
      });
      setStatus("");
    });

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;

      if (message.id && pending.has(message.id)) {
        const waiter = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) {
          waiter.reject(new Error(message.error.message || "Host returned an error."));
        } else {
          waiter.resolve(message.result);
        }
        return;
      }

      if (message.method === "ui/notifications/tool-result") {
        const next = message.params?.structuredContent;
        if (next && typeof next === "object") {
          data = unwrapToolOutput(next);
          render();
        }
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", () => {
      if (window.openai?.toolOutput) {
        data = unwrapToolOutput(window.openai.toolOutput);
        render();
      }
    }, { passive: true });

    render();
  </script>
</body>
</html>"""

MINERU_KEY_WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #eef3f7;
      --glass: rgba(255, 255, 255, .70);
      --glass-strong: rgba(255, 255, 255, .86);
      --input: rgba(255, 255, 255, .52);
      --text: #111827;
      --muted: #667085;
      --line: rgba(15, 23, 42, .13);
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --danger: #b42318;
      --shadow: 0 24px 70px rgba(15, 23, 42, .18);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --glass: rgba(22, 27, 34, .70);
        --glass-strong: rgba(31, 37, 46, .86);
        --input: rgba(15, 23, 42, .34);
        --text: #f8fafc;
        --muted: #aeb7c5;
        --line: rgba(226, 232, 240, .14);
        --accent: #2dd4bf;
        --accent-strong: #5eead4;
        --danger: #f97066;
        --shadow: 0 28px 80px rgba(0, 0, 0, .38);
      }
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(135deg, var(--bg), color-mix(in srgb, var(--bg), #dbeafe 28%));
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }

    main {
      min-height: 100vh;
      padding: 18px;
      display: grid;
      place-items: start center;
    }

    .panel {
      width: min(100%, 590px);
      background: linear-gradient(180deg, var(--glass-strong), var(--glass));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px) saturate(150%);
      -webkit-backdrop-filter: blur(22px) saturate(150%);
    }

    h1 {
      margin: 0 0 7px;
      font-size: 19px;
      font-weight: 700;
    }

    p {
      margin: 0 0 18px;
      color: var(--muted);
      max-width: 52ch;
    }

    label {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
      color: var(--text);
      font-weight: 650;
    }

    input {
      min-height: 42px;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--input);
      color: var(--text);
      padding: 9px 12px;
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .32);
    }

    input:focus {
      border-color: color-mix(in srgb, var(--accent), var(--line));
      outline: 3px solid color-mix(in srgb, var(--accent), transparent 74%);
      outline-offset: 0;
    }

    .row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }

    button {
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 8px;
      background: linear-gradient(180deg, var(--accent-strong), var(--accent));
      color: #fff;
      padding: 8px 14px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      box-shadow: 0 10px 24px rgba(15, 118, 110, .25);
      transition: transform .14s ease, filter .14s ease;
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      filter: brightness(1.04);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    .status {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
      white-space: pre-wrap;
    }

    .status.error { color: var(--danger); }

    @media (max-width: 560px) {
      main { padding: 10px; }
      .panel { padding: 16px; }
      .row { align-items: stretch; }
      .status { width: 100%; }
    }
  </style>
</head>
<body>
  <main>
    <form class="panel" id="form">
      <h1 id="title">Configure MinerU API key</h1>
      <p id="message">Enter your MinerU API key to enable official extract parsing.</p>
      <label>
        MinerU API key
        <input id="api-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste API key">
      </label>
      <div class="row">
        <button id="save" type="submit">Save key</button>
        <span class="status" id="status"></span>
      </div>
    </form>
  </main>
  <script>
    const form = document.getElementById("form");
    const input = document.getElementById("api-key");
    const button = document.getElementById("save");
    const message = document.getElementById("message");
    const statusNode = document.getElementById("status");
    let rpcId = 1;
    const pending = new Map();
    let data = window.openai?.toolOutput || {};

    function setStatus(text, kind = "") {
      statusNode.textContent = text || "";
      statusNode.className = kind ? `status ${kind}` : "status";
    }

    function render() {
      if (data.message) message.textContent = data.message;
      if (data.env_file_path) {
        input.setAttribute("aria-description", `Will save to ${data.env_file_path}`);
      }
    }

    function rpcRequest(method, params) {
      const id = rpcId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        window.setTimeout(() => {
          if (!pending.has(id)) return;
          pending.delete(id);
          reject(new Error("Timed out waiting for host response."));
        }, 60000);
      });
    }

    async function callTool(name, args) {
      if (window.openai?.callTool) {
        return window.openai.callTool(name, args);
      }
      return rpcRequest("tools/call", { name, arguments: args });
    }

    function structured(result) {
      return result?.structuredContent || result?.structured_content || result;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) {
        setStatus("Paste a MinerU API key first.", "error");
        return;
      }

      button.disabled = true;
      setStatus("Saving...");
      try {
        const result = await callTool("configure_mineru_api_key", {
          api_key: value,
        });
        const body = structured(result);
        input.value = "";
        setStatus(body?.message || "Saved.");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
      } finally {
        button.disabled = false;
      }
    });

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const payload = event.data;
      if (!payload || payload.jsonrpc !== "2.0") return;

      if (payload.id && pending.has(payload.id)) {
        const waiter = pending.get(payload.id);
        pending.delete(payload.id);
        if (payload.error) {
          waiter.reject(new Error(payload.error.message || "Host returned an error."));
        } else {
          waiter.resolve(payload.result);
        }
        return;
      }

      if (payload.method === "ui/notifications/tool-result") {
        const next = payload.params?.structuredContent;
        if (next && typeof next === "object") {
          data = next;
          render();
        }
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", () => {
      if (window.openai?.toolOutput) {
        data = window.openai.toolOutput;
        render();
      }
    }, { passive: true });

    render();
  </script>
</body>
</html>"""

# Instances of searchers
arxiv_searcher = ArxivSearcher()
pubmed_searcher = PubMedSearcher()
biorxiv_searcher = BioRxivSearcher()
medrxiv_searcher = MedRxivSearcher()
google_scholar_searcher = GoogleScholarSearcher()
iacr_searcher = IACRSearcher()
semantic_searcher = SemanticSearcher()
crossref_searcher = CrossRefSearcher()
openalex_searcher = OpenAlexSearcher()
pmc_searcher = PMCSearcher()
core_searcher = CORESearcher()
europepmc_searcher = EuropePMCSearcher()
dblp_searcher = DBLPSearcher()
openaire_searcher = OpenAiresearcher()
citeseerx_searcher = CiteSeerXSearcher()
doaj_searcher = DOAJSearcher()
base_searcher = BASESearcher()
unpaywall_resolver = UnpaywallResolver()
unpaywall_searcher = UnpaywallSearcher(resolver=unpaywall_resolver)
zenodo_searcher = ZenodoSearcher()
hal_searcher = HALSearcher()
ssrn_searcher = SSRNSearcher()
# scihub_searcher = SciHubSearcher()


def _wrap_save_path_methods(searcher: Any) -> None:
    """Expand ~/Desktop/papers-style defaults before source connectors touch paths."""
    if searcher is None or getattr(searcher, "_paper_search_save_path_wrapped", False):
        return

    for method_name in ("download_pdf", "read_paper"):
        original = getattr(searcher, method_name, None)
        if not callable(original):
            continue

        def _make_wrapper(method):
            def _wrapped(paper_id, save_path=DEFAULT_SAVE_PATH, *args, **kwargs):
                return method(paper_id, resolve_save_path(save_path), *args, **kwargs)

            return _wrapped

        setattr(searcher, method_name, _make_wrapper(original))

    setattr(searcher, "_paper_search_save_path_wrapped", True)


def _env_flag_enabled(name: str, default: str = "false") -> bool:
    value = get_env(name, default).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _custom_save_paths_allowed() -> bool:
    return _env_flag_enabled(ALLOW_CUSTOM_SAVE_PATH_ENV, default="true")


def _invalid_mcp_save_path(save_path: str) -> Optional[Dict[str, Any]]:
    """Return a structured error when custom MCP save paths are explicitly disabled."""
    requested = resolve_save_path(save_path)
    default = resolve_save_path(DEFAULT_SAVE_PATH)
    if requested == default or _custom_save_paths_allowed():
        return None

    return {
        "status": "invalid_save_path",
        "message": (
            f"MCP save_path overrides are disabled by PAPER_SEARCH_MCP_{ALLOW_CUSTOM_SAVE_PATH_ENV}=false. "
            f"Omit save_path to use {DEFAULT_SAVE_PATH}, remove the override, or set "
            f"PAPER_SEARCH_MCP_{ALLOW_CUSTOM_SAVE_PATH_ENV}=true to allow custom directories."
        ),
        "requested_save_path": requested,
        "default_save_path": default,
        "allow_env": f"PAPER_SEARCH_MCP_{ALLOW_CUSTOM_SAVE_PATH_ENV}",
    }


# Asynchronous helper to adapt synchronous searchers
# Runs blocking requests-based calls in a thread pool to avoid blocking the event loop.
async def async_search(searcher, query: str, max_results: int, **kwargs) -> List[Dict]:
    if 'year' in kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, year=kwargs['year'])
    elif kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [paper.to_dict() for paper in papers]


ALL_SOURCES = [
    "arxiv",
    "pubmed",
    "biorxiv",
    "medrxiv",
    "google_scholar",
    "iacr",
    "semantic",
    "crossref",
    "openalex",
    "pmc",
    "core",
    "europepmc",
    "dblp",
    "openaire",
    "citeseerx",
    "doaj",
    "base",
    "zenodo",
    "hal",
    "ssrn",
    "unpaywall",
]

FAST_SOURCES = [
    "arxiv",
    "semantic",
    "openalex",
    "crossref",
    "pubmed",
    "pmc",
    "europepmc",
]

DEEP_SOURCES = list(ALL_SOURCES)

SEARCH_PROFILES: Dict[str, List[str]] = {
    "fast": FAST_SOURCES,
    "default": FAST_SOURCES,
    "deep": DEEP_SOURCES,
    "all": ALL_SOURCES,
}


SOURCE_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "arxiv": {"search": True, "download": True, "read": True, "notes": "Open API; reliable PDF/read."},
    "pubmed": {"search": True, "download": False, "read": False, "notes": "Metadata only; use DOI/PMC fallback."},
    "biorxiv": {"search": True, "download": True, "read": True, "notes": "Recent category-filtered preprints."},
    "medrxiv": {"search": True, "download": True, "read": True, "notes": "Recent category-filtered preprints."},
    "google_scholar": {"search": True, "download": False, "read": False, "notes": "Discovery only; bot-detection prone."},
    "iacr": {"search": True, "download": True, "read": True, "notes": "IACR ePrint PDFs."},
    "semantic": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Works when an openAccessPdf URL is available."},
    "crossref": {"search": True, "download": False, "read": False, "notes": "DOI and metadata backbone."},
    "openalex": {"search": True, "download": False, "read": False, "notes": "Metadata and OA links; does not host PDFs."},
    "pmc": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Open-access PMC PDFs."},
    "core": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "CORE key recommended."},
    "europepmc": {"search": True, "download": "oa_pdf", "read": "oa_pdf", "notes": "Biomedical OA PDFs when available."},
    "dblp": {"search": True, "download": False, "read": False, "notes": "Computer science metadata only."},
    "openaire": {"search": True, "download": False, "read": False, "notes": "OA discovery links; direct tool is metadata-oriented."},
    "citeseerx": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "Upstream availability varies."},
    "doaj": {"search": True, "download": "url_dependent", "read": "url_dependent", "notes": "Open-access journal records."},
    "base": {"search": "institution_dependent", "download": "record_dependent", "read": "record_dependent", "notes": "OAI-PMH may need registered IP."},
    "zenodo": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "Open repository files."},
    "hal": {"search": True, "download": "record_dependent", "read": "record_dependent", "notes": "HAL documents with public PDF."},
    "ssrn": {"search": "best_effort", "download": "public_pdf_only", "read": "public_pdf_only", "notes": "SSRN bot/login restrictions vary."},
    "unpaywall": {"search": "doi_lookup", "download": False, "read": False, "notes": "OA URL resolver; requires email."},
}


# ---------------------------------------------------------------------------
# Optional paid-platform connectors (disabled by default)
# Set PAPER_SEARCH_MCP_IEEE_API_KEY / PAPER_SEARCH_MCP_ACM_API_KEY to activate
# (legacy IEEE_API_KEY / ACM_API_KEY are also supported).
# ---------------------------------------------------------------------------
_ieee_api_key = get_env("IEEE_API_KEY", "")
_acm_api_key = get_env("ACM_API_KEY", "")

if _ieee_api_key:
    from .academic_platforms.ieee import IEEESearcher
    ieee_searcher = IEEESearcher()
    ALL_SOURCES.append("ieee")
    DEEP_SOURCES.append("ieee")
    SOURCE_CAPABILITIES["ieee"] = {
        "search": "skeleton",
        "download": "skeleton",
        "read": "skeleton",
        "notes": "Registered only with key; implementation currently raises NotImplementedError.",
    }
    logger.info("IEEE Xplore enabled via configured environment key.")
else:
    ieee_searcher = None

if _acm_api_key:
    from .academic_platforms.acm import ACMSearcher
    acm_searcher = ACMSearcher()
    ALL_SOURCES.append("acm")
    DEEP_SOURCES.append("acm")
    SOURCE_CAPABILITIES["acm"] = {
        "search": "skeleton",
        "download": "skeleton",
        "read": "skeleton",
        "notes": "Registered only with key; implementation currently raises NotImplementedError.",
    }
    logger.info("ACM Digital Library enabled via configured environment key.")
else:
    acm_searcher = None


for _searcher in [
    arxiv_searcher,
    pubmed_searcher,
    biorxiv_searcher,
    medrxiv_searcher,
    google_scholar_searcher,
    iacr_searcher,
    semantic_searcher,
    crossref_searcher,
    openalex_searcher,
    pmc_searcher,
    core_searcher,
    europepmc_searcher,
    dblp_searcher,
    openaire_searcher,
    citeseerx_searcher,
    doaj_searcher,
    base_searcher,
    unpaywall_searcher,
    zenodo_searcher,
    hal_searcher,
    ssrn_searcher,
    ieee_searcher,
    acm_searcher,
]:
    _wrap_save_path_methods(_searcher)


def _parse_sources(sources: str) -> List[str]:
    value = (sources or "").strip().lower()
    if not value:
        value = get_env(SEARCH_PROFILE_ENV, "fast").strip().lower() or "fast"

    if value in SEARCH_PROFILES:
        return [source for source in SEARCH_PROFILES[value] if source in ALL_SOURCES]

    normalized = [part.strip().lower() for part in value.split(",") if part.strip()]
    return [source for source in normalized if source in ALL_SOURCES]


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = get_env(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = get_env(name, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _search_cache_key(query: str, max_results_per_source: int, sources: str, year: Optional[str]) -> str:
    resolved_sources = ",".join(_parse_sources(sources))
    return json.dumps(
        {
            "query": query,
            "max_results_per_source": max_results_per_source,
            "sources": resolved_sources,
            "year": year or "",
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _cached_search_result(cache_key: str) -> Optional[Dict[str, Any]]:
    ttl = _env_int(SEARCH_CACHE_TTL_ENV, 300, minimum=0)
    if ttl <= 0:
        return None
    entry = _SEARCH_RESULT_CACHE.get(cache_key)
    if not entry:
        return None
    if time.time() - float(entry.get("stored_at", 0)) > ttl:
        _SEARCH_RESULT_CACHE.pop(cache_key, None)
        return None
    payload = copy.deepcopy(entry.get("payload") or {})
    if payload:
        payload["cache"] = {"hit": True, "ttl_seconds": ttl}
    return payload


def _store_search_result(cache_key: str, payload: Dict[str, Any]) -> None:
    ttl = _env_int(SEARCH_CACHE_TTL_ENV, 300, minimum=0)
    if ttl <= 0:
        return
    _SEARCH_RESULT_CACHE[cache_key] = {"stored_at": time.time(), "payload": copy.deepcopy(payload)}


def _paper_unique_key(paper: Dict[str, Any]) -> str:
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"

    title = (paper.get("title") or "").strip().lower()
    authors = (paper.get("authors") or "").strip().lower()
    if title:
        return f"title:{title}|authors:{authors}"

    paper_id = (paper.get("paper_id") or "").strip().lower()
    return f"id:{paper_id}"


def _dedupe_papers(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for paper in papers:
        key = _paper_unique_key(paper)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(paper)

    return deduped


async def _search_source_with_timeout(
    source: str,
    operation: Any,
    timeout_seconds: float,
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        if timeout_seconds > 0:
            output = await asyncio.wait_for(operation, timeout=timeout_seconds)
        else:
            output = await operation
        return {
            "source": source,
            "output": output or [],
            "error": "",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except asyncio.TimeoutError:
        return {
            "source": source,
            "output": [],
            "error": f"timed out after {timeout_seconds:g}s",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "timed_out": True,
        }
    except Exception as exc:
        return {
            "source": source,
            "output": [],
            "error": str(exc),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }


def _paper_field(paper: Dict[str, Any], field: str) -> str:
    return str(paper.get(field) or "").strip()


def _paper_doi(paper: Dict[str, Any]) -> str:
    explicit = _paper_field(paper, "doi")
    if explicit:
        return explicit

    for field in ("paper_id", "url", "pdf_url"):
        recovered = extract_doi(_paper_field(paper, field))
        if recovered:
            return recovered
    return ""


def _paper_year(paper: Dict[str, Any]) -> str:
    for field in ("year", "published_date", "publication_date", "updated_date"):
        value = _paper_field(paper, field)
        if not value:
            continue
        match = re.search(r"(19|20)\d{2}", value)
        if match:
            return match.group(0)
    return ""


def _paper_parse_candidate(paper: Dict[str, Any], index: int) -> Dict[str, Any]:
    source = _paper_field(paper, "source").lower()
    paper_id = _paper_field(paper, "paper_id")
    doi = _paper_doi(paper)
    title = _paper_field(paper, "title")
    pdf_url = _paper_field(paper, "pdf_url")
    local_pdf_path = _paper_field(paper, "local_pdf_path") or _paper_field(paper, "pdf_path")
    url = _paper_field(paper, "url")

    download_capability = SOURCE_CAPABILITIES.get(source, {}).get("download")
    has_source_download = bool(source and paper_id and download_capability not in {False, None})

    if local_pdf_path:
        parse_ready = True
        reason = "local_pdf_path"
    elif pdf_url:
        parse_ready = True
        reason = "direct_pdf_url"
    elif has_source_download:
        parse_ready = True
        reason = "source_native_download"
    elif doi:
        parse_ready = True
        reason = "doi_oa_fallback"
    elif title:
        parse_ready = True
        reason = "title_repository_fallback"
    else:
        parse_ready = False
        reason = "missing_pdf_url_source_id_doi_title"

    return {
        "index": index,
        "title": title,
        "authors": _paper_field(paper, "authors"),
        "year": _paper_year(paper),
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "pdf_url": pdf_url,
        "local_pdf_path": local_pdf_path,
        "url": url,
        "parse_ready": parse_ready,
        "reason": reason,
    }


def _paper_selection_app_meta() -> Dict[str, Any]:
    return {
        "tool": PAPER_SELECTION_WIDGET_TOOL,
        "resource_uri": PAPER_SELECTION_WIDGET_URI,
        "output_template": PAPER_SELECTION_WIDGET_URI,
        "widget_accessible": True,
        "ui": {
            "resourceUri": PAPER_SELECTION_WIDGET_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
        "openai/widgetAccessible": True,
    }


def _paper_selection_app_payload(
    *,
    selection_token: str,
    papers: List[Dict[str, Any]],
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    message: str = "",
) -> Dict[str, Any]:
    parse_ready_total = sum(1 for paper in papers if paper.get("parse_ready") is not False)
    return {
        "status": "ok",
        "interaction": "mcp_app",
        "selection_token": selection_token,
        "papers": papers,
        "total": len(papers),
        "parse_ready_total": parse_ready_total,
        "save_path": save_path or DEFAULT_SAVE_PATH,
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "message": message or "Select papers in the checkbox UI, then parse selected papers.",
        "_meta": _paper_selection_app_meta(),
    }


def _paper_selection_app_prompt(
    *,
    selection_token: str,
    papers: List[Dict[str, Any]],
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    return {
        "interaction": "mcp_app",
        "instructions": (
            f"If the MCP host supports Apps, call {PAPER_SELECTION_WIDGET_TOOL} "
            "with this selection_token to render a checkbox UI."
        ),
        "render_tool": PAPER_SELECTION_WIDGET_TOOL,
        "resource_uri": PAPER_SELECTION_WIDGET_URI,
        "selection_token": selection_token,
        "save_path": save_path or DEFAULT_SAVE_PATH,
        "use_scihub": use_scihub,
        "mode": mode or "auto",
        "backend": backend or "",
        "force": force,
        "papers": papers,
        "total": len(papers),
        "parse_ready_total": sum(1 for paper in papers if paper.get("parse_ready") is not False),
    }


def _mineru_api_key_configured() -> bool:
    return bool(get_env("MINERU_API_KEY", "").strip())


def _mineru_key_app_meta() -> Dict[str, Any]:
    return {
        "tool": MINERU_KEY_WIDGET_TOOL,
        "resource_uri": MINERU_KEY_WIDGET_URI,
        "output_template": MINERU_KEY_WIDGET_URI,
        "widget_accessible": True,
        "ui": {
            "resourceUri": MINERU_KEY_WIDGET_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": MINERU_KEY_WIDGET_URI,
        "openai/widgetAccessible": True,
    }


def _mineru_key_setup_prompt(reason: str = "missing", message: str = "") -> Dict[str, Any]:
    detail = message or (
        "MinerU official extract parsing needs PAPER_SEARCH_MCP_MINERU_API_KEY. "
        f"Open {MINERU_KEY_WIDGET_TOOL} to enter and save the key."
    )
    return {
        "status": "mineru_api_key_required",
        "interaction": "mcp_app",
        "reason": reason,
        "message": detail,
        "render_tool": MINERU_KEY_WIDGET_TOOL,
        "resource_uri": MINERU_KEY_WIDGET_URI,
        "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
        "env_file_path": str(env_file_path()),
        "_meta": _mineru_key_app_meta(),
    }


def _is_mineru_api_key_error(message: str) -> bool:
    lowered = message.lower()
    needles = (
        "mineru_api_key",
        "api_key",
        "api key",
        "authorization",
        "unauthorized",
        "forbidden",
        "401",
        "403",
        "invalid token",
        "token expired",
        "permission",
    )
    return any(needle in lowered for needle in needles)


def _mineru_api_key_prompt_for_parse_result(parse_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if parse_result.get("status") not in {"error", "invalid_save_path"}:
        return None
    message = str(parse_result.get("message") or "")
    if not _mineru_api_key_configured():
        return _mineru_key_setup_prompt("missing", "MinerU API key is not configured. Enter it to enable extract parsing.")
    if _is_mineru_api_key_error(message):
        return _mineru_key_setup_prompt("expired_or_invalid", "MinerU API key appears invalid or expired. Enter a new key.")
    return None


def _attach_mineru_key_prompt(parse_result: Dict[str, Any]) -> Dict[str, Any]:
    prompt = _mineru_api_key_prompt_for_parse_result(parse_result)
    if prompt:
        parse_result = {**parse_result, "mineru_api_key_prompt": prompt}
    return parse_result


def _first_mineru_key_prompt(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        prompt = value.get("mineru_api_key_prompt")
        if isinstance(prompt, dict):
            return prompt
        for child in value.values():
            found = _first_mineru_key_prompt(child)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_mineru_key_prompt(item)
            if found:
                return found
    return None


def _parse_selected_indices(selected_indices: Any, max_index: int) -> List[int]:
    if max_index <= 0:
        return []

    raw_items: List[Any] = []
    if isinstance(selected_indices, str):
        value = selected_indices.strip().lower()
        if value in {"all", "*"}:
            return list(range(1, max_index + 1))
        raw_items = [part for part in re.split(r"[,\s]+", value) if part]
    elif isinstance(selected_indices, int):
        raw_items = [selected_indices]
    elif isinstance(selected_indices, (list, tuple, set)):
        raw_items = list(selected_indices)
    else:
        raise ValueError("selected_indices must be 'all', a comma-separated string, or a list of numbers")

    selected: List[int] = []
    for item in raw_items:
        if isinstance(item, str) and re.fullmatch(r"\d+\s*-\s*\d+", item):
            start_s, end_s = re.split(r"\s*-\s*", item, maxsplit=1)
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            selected.extend(range(start, end + 1))
            continue

        try:
            selected.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid selection index: {item}") from exc

    deduped: List[int] = []
    for index in selected:
        if index < 1 or index > max_index:
            raise ValueError(f"Selection index {index} is outside 1..{max_index}")
        if index not in deduped:
            deduped.append(index)

    if not deduped:
        raise ValueError("No selected indices provided")
    return deduped


def _shorten_for_option(value: str, limit: int = 120) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def _elicitation_option_label(candidate: Dict[str, Any]) -> str:
    index = candidate.get("index", "")
    title = _shorten_for_option(str(candidate.get("title") or "Untitled paper"), 140)
    source = str(candidate.get("source") or "unknown")
    year = str(candidate.get("year") or "n.d.")
    doi = str(candidate.get("doi") or "")
    paper_id = str(candidate.get("paper_id") or "")
    identifier = doi or paper_id or str(candidate.get("url") or "")
    suffix = f"{source}, {year}"
    if identifier:
        suffix = f"{suffix}, {identifier}"
    return _shorten_for_option(f"{index}. {title} [{suffix}]", 220)


def _build_paper_selection_schema(options: List[str]) -> type:
    return create_model(
        "PaperSelectionElicitation",
        selected_papers=(
            List[str],
            Field(
                default_factory=list,
                title="Papers to parse",
                description="Select one or more papers for MinerU PDF parsing.",
                json_schema_extra={
                    "items": {"type": "string", "enum": options},
                    "uniqueItems": True,
                },
            ),
        ),
    )


def _pdf_path_from_result(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    path = Path(value).expanduser()
    if path.exists() and path.is_file() and path.suffix.lower() == ".pdf":
        return str(path.resolve())
    return ""


def _pdf_paths_from_result(value: Any) -> List[str]:
    if isinstance(value, dict):
        raw_paths: List[Any] = []
        for key in ("pdf_path", "local_pdf_path"):
            raw_paths.append(value.get(key))
        raw_paths.extend(value.get("pdf_paths") or [])
        paths: List[str] = []
        for item in raw_paths:
            path = _pdf_path_from_result(item)
            if path and path not in paths:
                paths.append(path)
        return paths

    path = _pdf_path_from_result(value)
    return [path] if path else []


def _snapshot_pdf_files(save_path: str) -> Dict[str, tuple[int, int]]:
    root = Path(resolve_save_path(save_path))
    if not root.exists() or not root.is_dir():
        return {}

    snapshot: Dict[str, tuple[int, int]] = {}
    for path in root.rglob("*.pdf"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[str(path.resolve())] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _changed_pdf_paths(before: Dict[str, tuple[int, int]], save_path: str) -> List[str]:
    after = _snapshot_pdf_files(save_path)
    changed: List[str] = []
    for path, signature in after.items():
        if before.get(path) != signature:
            changed.append(path)
    return sorted(changed)


def _downloaded_pdf_paper(
    *,
    pdf_path: str,
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
) -> Dict[str, Any]:
    path = Path(pdf_path).expanduser().resolve()
    return {
        "title": title or path.stem,
        "authors": "",
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "pdf_url": "",
        "local_pdf_path": str(path),
        "url": "",
    }


def _downloaded_pdf_papers(
    pdf_paths: List[str],
    *,
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
) -> List[Dict[str, Any]]:
    papers: List[Dict[str, Any]] = []
    for index, pdf_path in enumerate(pdf_paths):
        item_title = title
        if len(pdf_paths) > 1:
            item_title = f"{title or Path(pdf_path).stem} ({index + 1})"
        papers.append(
            _downloaded_pdf_paper(
                pdf_path=pdf_path,
                source=source,
                paper_id=paper_id,
                doi=doi,
                title=item_title,
            )
        )
    return papers


async def _prompt_parse_saved_pdfs(
    *,
    papers: List[Dict[str, Any]],
    query: str,
    sources: str,
    save_path: str,
    ctx: Optional[Context],
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    session = await asyncio.to_thread(
        cache_create_search_session,
        query,
        sources,
        papers,
        {
            "interaction": "download_saved_pdf_parse_prompt",
            "trigger": "pdf_saved",
            "save_path": resolve_save_path(save_path),
        },
    )
    candidates = [_paper_parse_candidate(paper, index + 1) for index, paper in enumerate(papers)]
    selectable = [candidate for candidate in candidates if candidate.get("parse_ready")]

    fallback = {
        "status": "elicitation_unavailable",
        "interaction": "backend_session_numbered_selection",
        "selection_token": session["selection_token"],
        "instructions": (
            "PDF saved. Present the numbered papers to the user. To parse selected PDFs, "
            "call parse_selected_papers(selection_token=<token>, selected_indices='1') "
            "or selected_indices='all'."
        ),
        "papers": candidates,
        "total": len(candidates),
        "parse_ready_total": len(selectable),
    }
    fallback["app"] = _paper_selection_app_prompt(
        selection_token=session["selection_token"],
        papers=candidates,
        save_path=save_path,
        mode=mode,
        backend=backend,
        force=force,
    )

    if not selectable:
        return {**fallback, "status": "no_parse_ready_pdfs"}

    if len(candidates) <= AUTO_PARSE_SAVED_PDF_LIMIT:
        selected_indices = [int(candidate["index"]) for candidate in selectable]
        parse_result = await parse_selected_papers(
            selection_token=session["selection_token"],
            selected_indices=",".join(str(index) for index in selected_indices),
            save_path=save_path,
            use_scihub=False,
            mode=mode,
            backend=backend,
            force=force,
        )
        return {
            **parse_result,
            "interaction": "auto_parse_saved_pdfs",
            "selection_token": session["selection_token"],
            "selected_indices": selected_indices,
            "auto_parse_limit": AUTO_PARSE_SAVED_PDF_LIMIT,
            "message": (
                f"Saved {len(candidates)} PDF(s), which is at or below "
                f"the auto-parse limit of {AUTO_PARSE_SAVED_PDF_LIMIT}. Parsed all saved PDFs."
            ),
        }

    if ctx is None:
        return fallback

    options = [_elicitation_option_label(candidate) for candidate in selectable]
    schema = _build_paper_selection_schema(options)
    try:
        elicitation = await ctx.elicit(
            message="PDF saved. Select PDFs for MinerU PDF parsing.",
            schema=schema,
        )
    except Exception as exc:
        return {**fallback, "message": f"Elicitation request failed: {exc}"}

    if getattr(elicitation, "action", "") != "accept":
        return {
            **fallback,
            "status": "elicitation_not_accepted",
            "elicitation_action": getattr(elicitation, "action", ""),
            "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed.",
        }

    selected_values = getattr(getattr(elicitation, "data", None), "selected_papers", [])
    try:
        selected_indices = _parse_elicitation_selected_indices(selected_values, len(candidates))
    except ValueError as exc:
        return {**fallback, "status": "invalid_elicitation_selection", "message": str(exc)}

    if not selected_indices:
        return {
            **fallback,
            "status": "no_selection",
            "message": "No PDFs were selected. Use parse_selected_papers with numbered indices if needed.",
        }

    parse_result = await parse_selected_papers(
        selection_token=session["selection_token"],
        selected_indices=",".join(str(index) for index in selected_indices),
        save_path=save_path,
        use_scihub=False,
        mode=mode,
        backend=backend,
        force=force,
    )
    return {
        **parse_result,
        "interaction": "elicitation",
        "selection_token": session["selection_token"],
        "selected_indices": selected_indices,
    }


async def _after_saved_pdf(
    result: Any,
    *,
    source: str,
    paper_id: str,
    save_path: str,
    downloader: str,
    doi: str = "",
    title: str = "",
    legal_status: str = "source_native_or_open_access",
    ctx: Optional[Context] = None,
) -> Any:
    pdf_paths = _pdf_paths_from_result(result)
    if not pdf_paths:
        return result

    for pdf_path in pdf_paths:
        record_download(
            pdf_path=pdf_path,
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title or Path(pdf_path).stem,
            downloader=downloader,
            legal_status=legal_status,
        )

    papers = _downloaded_pdf_papers(
        pdf_paths,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
    )
    parse_prompt = await _prompt_parse_saved_pdfs(
        papers=papers,
        query=title or paper_id or Path(pdf_paths[0]).stem,
        sources=source,
        save_path=save_path,
        ctx=ctx,
    )
    response = {
        "status": "downloaded",
        "pdf_path": pdf_paths[0],
        "pdf_paths": pdf_paths,
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "title": title or Path(pdf_paths[0]).stem,
        "parse_prompt": parse_prompt,
    }
    if isinstance(parse_prompt, dict) and isinstance(parse_prompt.get("app"), dict):
        response["app"] = parse_prompt["app"]
    return response


async def _after_saved_pdfs(
    pdf_paths: List[str],
    *,
    source: str,
    paper_id: str,
    save_path: str,
    downloader: str,
    doi: str = "",
    title: str = "",
    legal_status: str = "source_native_or_open_access",
    ctx: Optional[Context] = None,
) -> Optional[Dict[str, Any]]:
    normalized: List[str] = []
    for pdf_path in pdf_paths:
        resolved = _pdf_path_from_result(pdf_path)
        if resolved and resolved not in normalized:
            normalized.append(resolved)

    if not normalized:
        return None

    return await _after_saved_pdf(
        {"pdf_paths": normalized},
        source=source,
        paper_id=paper_id,
        save_path=save_path,
        downloader=downloader,
        doi=doi,
        title=title,
        legal_status=legal_status,
        ctx=ctx,
    )


async def _download_source_pdf(
    searcher: Any,
    *,
    source: str,
    paper_id: str,
    save_path: str,
    ctx: Optional[Context] = None,
    doi: str = "",
    title: str = "",
    downloader: str = "",
    legal_status: str = "source_native_or_open_access",
) -> Any:
    invalid_save_path = _invalid_mcp_save_path(save_path)
    if invalid_save_path:
        return invalid_save_path

    save_path = resolve_save_path(save_path)
    try:
        result = await asyncio.to_thread(searcher.download_pdf, paper_id, save_path)
    except NotImplementedError as exc:
        return str(exc)

    return await _after_saved_pdf(
        result,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
        downloader=downloader or f"{source}.download_pdf",
        legal_status=legal_status,
        ctx=ctx,
    )


async def _read_source_paper(
    searcher: Any,
    *,
    source: str,
    paper_id: str,
    save_path: str,
    ctx: Optional[Context] = None,
    doi: str = "",
    title: str = "",
) -> Any:
    invalid_save_path = _invalid_mcp_save_path(save_path)
    if invalid_save_path:
        return invalid_save_path

    resolved_save_path = resolve_save_path(save_path)
    before = _snapshot_pdf_files(resolved_save_path)
    try:
        result = await asyncio.to_thread(searcher.read_paper, paper_id, resolved_save_path)
    except Exception as exc:
        logger.warning("Read failed for %s/%s: %s", source, paper_id, exc)
        return ""

    changed_pdfs = _changed_pdf_paths(before, resolved_save_path)
    parse_prompt = await _after_saved_pdfs(
        changed_pdfs,
        source=source,
        paper_id=paper_id,
        save_path=resolved_save_path,
        downloader=f"{source}.read_paper",
        doi=doi,
        title=title,
        ctx=ctx,
    )
    if parse_prompt is None:
        return result

    return {
        "status": "read",
        "source": source,
        "paper_id": paper_id,
        "doi": doi,
        "title": title,
        "text": result,
        "saved_pdf_prompt": parse_prompt,
    }


def _parse_elicitation_selected_indices(selected_values: Any, max_index: int) -> List[int]:
    if selected_values is None:
        return []
    if isinstance(selected_values, str):
        selected_values = [selected_values]

    indices: List[int] = []
    for value in selected_values:
        text = str(value).strip()
        match = re.match(r"^(\d+)(?:[.\s]|$)", text)
        if match:
            indices.append(int(match.group(1)))
            continue
        if text.isdigit():
            indices.append(int(text))
            continue
        raise ValueError(f"Unable to parse selected paper option: {text}")

    return _parse_selected_indices(indices, max_index) if indices else []


def _safe_filename(filename_hint: str, default: str = "paper") -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename_hint).strip("._")
    if not safe:
        return default
    return safe[:120]


def _looks_like_pdf_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "\n" in text or "\r" in text:
        return False
    if os.path.exists(text):
        return True
    return text.lower().endswith(".pdf")


async def _download_from_url(pdf_url: str, save_path: str, filename_hint: str = "paper") -> Optional[str]:
    if not pdf_url:
        return None

    save_path = resolve_save_path(save_path)
    os.makedirs(save_path, exist_ok=True)
    output_name = f"{_safe_filename(filename_hint)}.pdf"
    output_path = os.path.join(save_path, output_name)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(pdf_url)

        if response.status_code >= 400 or not response.content:
            return None

        content_type = (response.headers.get("content-type") or "").lower()
        is_pdf = "pdf" in content_type or response.content.startswith(b"%PDF") or pdf_url.lower().endswith(".pdf")
        if not is_pdf:
            logger.warning("Resolved URL is not a PDF candidate: %s (content-type=%s)", pdf_url, content_type)
            return None

        with open(output_path, "wb") as file_obj:
            file_obj.write(response.content)

        return output_path
    except Exception as exc:
        logger.warning("Direct URL download failed for %s: %s", pdf_url, exc)
        return None


async def _try_repository_fallback(doi: str, title: str, save_path: str) -> tuple[Optional[str], str]:
    repository_searchers = [
        ("openaire", openaire_searcher),
        ("core", core_searcher),
        ("europepmc", europepmc_searcher),
        ("pmc", pmc_searcher),
    ]

    query_candidates = [(doi or "").strip(), (title or "").strip()]
    query_candidates = [candidate for candidate in query_candidates if candidate]
    if not query_candidates:
        return None, "no DOI/title provided for repository fallback"

    repository_errors: List[str] = []

    for repo_name, searcher in repository_searchers:
        for query in query_candidates:
            try:
                papers = await asyncio.to_thread(searcher.search, query, max_results=3)
            except Exception as exc:
                repository_errors.append(f"{repo_name}:{exc}")
                continue

            if not papers:
                continue

            for paper in papers:
                pdf_url = str(getattr(paper, "pdf_url", "") or "").strip()
                if not pdf_url:
                    continue

                raw_paper_id = getattr(paper, "paper_id", "")
                paper_id = str(raw_paper_id or query).strip()
                downloaded = await _download_from_url(pdf_url, save_path, f"{repo_name}_{paper_id}")
                if downloaded:
                    return downloaded, ""

    return None, "; ".join(repository_errors)


def _primary_downloaders() -> Dict[str, Any]:
    return {
        "arxiv": arxiv_searcher.download_pdf,
        "biorxiv": biorxiv_searcher.download_pdf,
        "medrxiv": medrxiv_searcher.download_pdf,
        "iacr": iacr_searcher.download_pdf,
        "semantic": semantic_searcher.download_pdf,
        "pubmed": pubmed_searcher.download_pdf,
        "crossref": crossref_searcher.download_pdf,
        "pmc": pmc_searcher.download_pdf,
        "core": core_searcher.download_pdf,
        "europepmc": europepmc_searcher.download_pdf,
        "citeseerx": citeseerx_searcher.download_pdf,
        "doaj": doaj_searcher.download_pdf,
        "base": base_searcher.download_pdf,
        "zenodo": zenodo_searcher.download_pdf,
        "hal": hal_searcher.download_pdf,
        "ssrn": ssrn_searcher.download_pdf,
    }


async def _try_primary_download(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
) -> Dict[str, Any]:
    downloader = _primary_downloaders().get(source_name)
    if downloader is None:
        return {
            "method": "primary",
            "path": None,
            "error": f"Unsupported source '{source_name}' for primary download.",
        }

    try:
        primary_result = await asyncio.to_thread(downloader, paper_id, save_path)
    except Exception as exc:
        logger.warning("Primary download failed for %s/%s: %s", source_name, paper_id, exc)
        return {"method": "primary", "path": None, "error": str(exc)}

    if _looks_like_pdf_path(primary_result):
        if os.path.exists(primary_result):
            record_download(
                pdf_path=primary_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader=f"{source_name}.download_pdf",
                legal_status="source_native_or_open_access",
            )
        return {
            "method": "primary",
            "path": primary_result,
            "downloader": f"{source_name}.download_pdf",
            "legal_status": "source_native_or_open_access",
        }

    return {"method": "primary", "path": None, "error": str(primary_result or "no PDF returned")}


async def _try_repository_download(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
) -> Dict[str, Any]:
    repository_result, repository_error = await _try_repository_fallback(doi, title, save_path)
    if repository_result:
        if os.path.exists(repository_result):
            record_download(
                pdf_path=repository_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader="repository_fallback",
                legal_status="open_access_repository",
            )
        return {
            "method": "repositories",
            "path": repository_result,
            "downloader": "repository_fallback",
            "legal_status": "open_access_repository",
        }
    return {"method": "repositories", "path": None, "error": repository_error or "no repository PDF found"}


async def _try_unpaywall_download(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
) -> Dict[str, Any]:
    normalized_doi = (doi or "").strip()
    if not normalized_doi:
        return {"method": "unpaywall", "path": None, "error": "DOI not provided"}

    try:
        unpaywall_url = await asyncio.to_thread(unpaywall_resolver.resolve_best_pdf_url, normalized_doi)
    except Exception as exc:
        return {"method": "unpaywall", "path": None, "error": str(exc)}

    if not unpaywall_url:
        return {
            "method": "unpaywall",
            "path": None,
            "error": "no OA URL found (or PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL missing)",
        }

    unpaywall_result = await _download_from_url(unpaywall_url, save_path, f"unpaywall_{normalized_doi}")
    if unpaywall_result:
        if os.path.exists(unpaywall_result):
            record_download(
                pdf_path=unpaywall_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader="unpaywall",
                legal_status="open_access_unpaywall",
            )
        return {
            "method": "unpaywall",
            "path": unpaywall_result,
            "downloader": "unpaywall",
            "legal_status": "open_access_unpaywall",
        }
    return {"method": "unpaywall", "path": None, "error": "resolved OA URL but download failed"}


async def _race_oa_downloads(
    *,
    source_name: str,
    paper_id: str,
    doi: str,
    title: str,
    save_path: str,
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    task_specs = [
        (
            "primary",
            _try_primary_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
            ),
        ),
        (
            "repositories",
            _try_repository_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
            ),
        ),
        (
            "unpaywall",
            _try_unpaywall_download(
                source_name=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                save_path=save_path,
            ),
        ),
    ]
    tasks = {asyncio.create_task(coro): name for name, coro in task_specs}
    errors: List[str] = []
    try:
        for completed in asyncio.as_completed(tasks):
            try:
                result = await completed
            except Exception as exc:
                errors.append(str(exc))
                continue
            method = str(result.get("method") or tasks.get(completed) or "download")
            path = result.get("path")
            if _looks_like_pdf_path(path):
                for task in tasks:
                    if not task.done():
                        task.cancel()
                return result, errors
            if result.get("error"):
                errors.append(f"{method}: {result['error']}")
    finally:
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return None, errors


@mcp.tool()
async def search_papers(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "",
    year: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified top-level search across all configured academic platforms.

    Args:
        query: Search query string.
        max_results_per_source: Max results to fetch from each selected source.
        sources: Comma-separated source names or 'all'.
            Available: arxiv,pubmed,biorxiv,medrxiv,google_scholar,iacr,semantic,crossref,openalex,pmc,core,europepmc,dblp,openaire,citeseerx,doaj,base,zenodo,hal,ssrn,unpaywall
        year: Optional year filter for Semantic Scholar only.
    Returns:
        Aggregated dictionary with per-source stats, errors, and deduplicated papers.
    """
    started = time.perf_counter()
    selected_sources = _parse_sources(sources)
    cache_key = _search_cache_key(query, max_results_per_source, sources, year)
    cached = _cached_search_result(cache_key)
    if cached is not None:
        return cached

    if not selected_sources:
        return {
            "query": query,
            "sources_requested": sources,
            "sources_used": [],
            "source_results": {},
            "errors": {"sources": "No valid sources selected."},
            "papers": [],
            "total": 0,
        }

    task_map = {}
    for source in selected_sources:
        if source == "arxiv":
            task_map[source] = search_arxiv(query, max_results_per_source)
        elif source == "pubmed":
            task_map[source] = search_pubmed(query, max_results_per_source)
        elif source == "biorxiv":
            task_map[source] = search_biorxiv(query, max_results_per_source)
        elif source == "medrxiv":
            task_map[source] = search_medrxiv(query, max_results_per_source)
        elif source == "google_scholar":
            task_map[source] = search_google_scholar(query, max_results_per_source)
        elif source == "iacr":
            task_map[source] = search_iacr(query, max_results_per_source, fetch_details=False)
        elif source == "semantic":
            task_map[source] = search_semantic(query, year=year, max_results=max_results_per_source)
        elif source == "crossref":
            task_map[source] = search_crossref(query, max_results=max_results_per_source)
        elif source == "openalex":
            task_map[source] = search_openalex(query, max_results_per_source)
        elif source == "pmc":
            task_map[source] = search_pmc(query, max_results_per_source)
        elif source == "core":
            task_map[source] = search_core(query, max_results_per_source)
        elif source == "europepmc":
            task_map[source] = search_europepmc(query, max_results_per_source)
        elif source == "dblp":
            task_map[source] = search_dblp(query, max_results_per_source)
        elif source == "openaire":
            task_map[source] = search_openaire(query, max_results_per_source)
        elif source == "citeseerx":
            task_map[source] = search_citeseerx(query, max_results_per_source)
        elif source == "doaj":
            task_map[source] = search_doaj(query, max_results_per_source)
        elif source == "base":
            task_map[source] = search_base(query, max_results_per_source)
        elif source == "zenodo":
            task_map[source] = search_zenodo(query, max_results_per_source)
        elif source == "hal":
            task_map[source] = search_hal(query, max_results_per_source)
        elif source == "ssrn":
            task_map[source] = search_ssrn(query, max_results_per_source)
        elif source == "unpaywall":
            task_map[source] = search_unpaywall(query, max_results_per_source)
        elif source == "ieee":
            if ieee_searcher is not None:
                task_map[source] = async_search(ieee_searcher, query, max_results_per_source)
        elif source == "acm":
            if acm_searcher is not None:
                task_map[source] = async_search(acm_searcher, query, max_results_per_source)

    source_names = list(task_map.keys())
    per_source_timeout = _env_float(SEARCH_SOURCE_TIMEOUT_ENV, 12.0, minimum=0.0)
    overall_timeout = _env_float(SEARCH_TIMEOUT_ENV, 18.0, minimum=0.0)
    source_tasks = [
        asyncio.create_task(_search_source_with_timeout(source, task_map[source], per_source_timeout))
        for source in source_names
    ]
    try:
        if overall_timeout > 0:
            source_outputs = await asyncio.wait_for(
                asyncio.gather(*source_tasks, return_exceptions=True),
                timeout=overall_timeout,
            )
        else:
            source_outputs = await asyncio.gather(*source_tasks, return_exceptions=True)
    except asyncio.TimeoutError:
        source_outputs = []
        for source, task in zip(source_names, source_tasks):
            if task.done():
                if task.cancelled():
                    source_outputs.append(
                        {
                            "source": source,
                            "output": [],
                            "error": f"overall search timed out after {overall_timeout:g}s",
                            "timed_out": True,
                        }
                    )
                    continue
                try:
                    source_outputs.append(task.result())
                except Exception as exc:
                    source_outputs.append({"source": source, "output": [], "error": str(exc)})
            else:
                task.cancel()
                source_outputs.append(
                    {
                        "source": source,
                        "output": [],
                        "error": f"overall search timed out after {overall_timeout:g}s",
                        "timed_out": True,
                    }
                )

    source_results: Dict[str, int] = {}
    errors: Dict[str, str] = {}
    source_timings: Dict[str, float] = {}
    timed_out_sources: List[str] = []
    merged_papers: List[Dict[str, Any]] = []

    for source_name, output in zip(source_names, source_outputs):
        if isinstance(output, Exception):
            errors[source_name] = str(output)
            source_results[source_name] = 0
            continue

        if not isinstance(output, dict):
            errors[source_name] = f"unexpected search result: {output!r}"
            source_results[source_name] = 0
            continue

        papers = output.get("output", []) or []
        source_results[source_name] = len(papers)
        if output.get("error"):
            errors[source_name] = str(output["error"])
        if output.get("timed_out"):
            timed_out_sources.append(source_name)
        if "elapsed_seconds" in output:
            source_timings[source_name] = float(output["elapsed_seconds"])

        for paper in papers:
            if not paper.get("source"):
                paper["source"] = source_name
            merged_papers.append(paper)

    deduped_papers = _dedupe_papers(merged_papers)

    result = {
        "query": query,
        "sources_requested": sources,
        "sources_used": source_names,
        "source_results": source_results,
        "errors": errors,
        "timed_out_sources": timed_out_sources,
        "source_timings": source_timings,
        "papers": deduped_papers,
        "total": len(deduped_papers),
        "raw_total": len(merged_papers),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cache": {"hit": False, "ttl_seconds": _env_int(SEARCH_CACHE_TTL_ENV, 300, minimum=0)},
    }
    _store_search_result(cache_key, result)
    return result


@mcp.tool()
async def search_papers_for_parsing(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "",
    year: Optional[str] = None,
) -> Dict[str, Any]:
    """Search papers, persist a numbered selection session, and return parse candidates.

    Use this when the MCP client cannot show elicitation/App checkbox UI. The
    caller can present the returned numbered list, then call
    parse_selected_papers with the selection_token and indices like "1,3,5".
    """
    search_result = await search_papers(
        query=query,
        max_results_per_source=max_results_per_source,
        sources=sources,
        year=year,
    )
    papers = search_result.get("papers", [])
    if not isinstance(papers, list):
        papers = []

    session = await asyncio.to_thread(
        cache_create_search_session,
        query,
        sources,
        papers,
        {
            "year": year or "",
            "max_results_per_source": max_results_per_source,
            "sources_used": search_result.get("sources_used", []),
            "source_results": search_result.get("source_results", {}),
            "errors": search_result.get("errors", {}),
            "interaction": "backend_session_numbered_selection",
        },
    )
    candidates = [_paper_parse_candidate(paper, index + 1) for index, paper in enumerate(papers)]
    parse_ready_total = sum(1 for candidate in candidates if candidate["parse_ready"])

    result = {
        "status": "ok",
        "selection_token": session["selection_token"],
        "query": query,
        "sources_requested": sources,
        "sources_used": search_result.get("sources_used", []),
        "source_results": search_result.get("source_results", {}),
        "errors": search_result.get("errors", {}),
        "instructions": (
            "Present the numbered papers to the user. To parse selected papers, "
            "call parse_selected_papers(selection_token=<token>, selected_indices='1,3,5') "
            "or selected_indices='all'."
        ),
        "papers": candidates,
        "total": len(candidates),
        "parse_ready_total": parse_ready_total,
        "raw_total": search_result.get("raw_total", len(candidates)),
    }
    result["app"] = _paper_selection_app_prompt(
        selection_token=session["selection_token"],
        papers=candidates,
    )
    return result


@mcp.resource(
    PAPER_SELECTION_WIDGET_URI,
    name="Paper Selection Widget",
    mime_type="text/html;profile=mcp-app",
    meta={
        "ui": {
            "prefersBorder": True,
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
            },
        },
        "openai/widgetDescription": "Checkbox selector for choosing saved or searched papers to parse with MinerU.",
        "openai/widgetPrefersBorder": True,
        "openai/widgetCSP": {
            "connect_domains": [],
            "resource_domains": [],
        },
    },
)
async def paper_selection_widget() -> str:
    """Return the checkbox UI used by MCP Apps-capable hosts."""
    return PAPER_SELECTION_WIDGET_HTML


@mcp.tool(
    name=PAPER_SELECTION_WIDGET_TOOL,
    meta={
        "ui": {"resourceUri": PAPER_SELECTION_WIDGET_URI, "visibility": ["model", "app"]},
        "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": "Opening paper selector...",
        "openai/toolInvocation/invoked": "Paper selector ready.",
    },
    structured_output=True,
)
async def render_paper_selection_app(
    selection_token: str,
    papers: Optional[List[Dict[str, Any]]] = None,
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Render a checkbox paper selector for MCP Apps-capable hosts.

    If papers are omitted, the tool loads candidates from the saved selection
    session identified by selection_token.
    """
    candidates = papers if isinstance(papers, list) else []
    if not candidates:
        session = await asyncio.to_thread(cache_get_search_session, selection_token)
        stored_papers = session.get("papers", []) if session else []
        if not isinstance(stored_papers, list):
            stored_papers = []
        candidates = [_paper_parse_candidate(paper, index + 1) for index, paper in enumerate(stored_papers)]
    return _paper_selection_app_payload(
        selection_token=selection_token,
        papers=candidates,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
    )


@mcp.tool(name=LOCAL_PAPER_SELECTION_TOOL, structured_output=True)
async def open_paper_selection_page(
    selection_token: str,
    papers: Optional[List[Dict[str, Any]]] = None,
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    open_browser: bool = True,
) -> Dict[str, Any]:
    """Open a local browser checkbox selector for clients without MCP Apps UI.

    This fallback renders a normal localhost HTML page, so it works even when
    the chat host cannot display MCP Apps widgets in the conversation.
    """
    candidates = papers if isinstance(papers, list) else []
    if not candidates:
        session = await asyncio.to_thread(cache_get_search_session, selection_token)
        stored_papers = session.get("papers", []) if session else []
        if not isinstance(stored_papers, list):
            stored_papers = []
        candidates = [_paper_parse_candidate(paper, index + 1) for index, paper in enumerate(stored_papers)]

    page = _create_local_selection_page(
        selection_token=selection_token,
        papers=candidates,
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
    )
    opened = False
    if open_browser:
        opened = await asyncio.to_thread(webbrowser.open, page["url"])

    return {
        "status": "ok",
        "interaction": "local_browser_checkbox",
        "selection_token": selection_token,
        "url": page["url"],
        "page_id": page["page_id"],
        "opened": opened,
        "papers": candidates,
        "total": len(candidates),
        "parse_ready_total": sum(1 for paper in candidates if paper.get("parse_ready") is not False),
        "message": "Open the URL to select papers with checkboxes and parse them from the browser page.",
    }


@mcp.resource(
    MINERU_KEY_WIDGET_URI,
    name="MinerU API Key Setup Widget",
    mime_type="text/html;profile=mcp-app",
    meta={
        "ui": {
            "prefersBorder": True,
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
            },
        },
        "openai/widgetDescription": "Form for saving PAPER_SEARCH_MCP_MINERU_API_KEY to the project .env file.",
        "openai/widgetPrefersBorder": True,
        "openai/widgetCSP": {
            "connect_domains": [],
            "resource_domains": [],
        },
    },
)
async def mineru_api_key_setup_widget() -> str:
    """Return the MinerU API key setup UI for MCP Apps-capable hosts."""
    return MINERU_KEY_WIDGET_HTML


@mcp.tool(
    name=MINERU_KEY_WIDGET_TOOL,
    meta={
        "ui": {"resourceUri": MINERU_KEY_WIDGET_URI, "visibility": ["model", "app"]},
        "openai/outputTemplate": MINERU_KEY_WIDGET_URI,
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": "Opening MinerU setup...",
        "openai/toolInvocation/invoked": "MinerU setup ready.",
    },
    structured_output=True,
)
async def render_mineru_api_key_setup_app(reason: str = "missing", message: str = "") -> Dict[str, Any]:
    """Render a MinerU API key setup form for MCP Apps-capable hosts."""
    prompt = _mineru_key_setup_prompt(reason=reason, message=message)
    return {
        **prompt,
        "configured": _mineru_api_key_configured(),
    }


@mcp.tool(name=MINERU_KEY_CONFIG_TOOL, structured_output=True)
async def configure_mineru_api_key(api_key: str) -> Dict[str, Any]:
    """Persist PAPER_SEARCH_MCP_MINERU_API_KEY to the project .env file."""
    value = api_key.strip()
    if not value:
        return {
            "status": "invalid_api_key",
            "message": "MinerU API key cannot be empty.",
            "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
        }

    target = await asyncio.to_thread(set_env_value, "MINERU_API_KEY", value)
    return {
        "status": "ok",
        "message": "MinerU API key saved. New parse requests will use the updated key.",
        "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
        "env_file_path": str(target),
        "configured": True,
    }


@mcp.tool(
    meta={
        "ui": {"resourceUri": MINERU_KEY_WIDGET_URI, "visibility": ["model", "app"]},
        "openai/outputTemplate": MINERU_KEY_WIDGET_URI,
        "openai/widgetAccessible": True,
    },
    structured_output=True,
)
async def mineru_setup_status() -> Dict[str, Any]:
    """Return MinerU API key setup status and an Apps prompt when configuration is missing."""
    configured = _mineru_api_key_configured()
    if configured:
        return {
            "status": "ok",
            "configured": True,
            "env_key": "PAPER_SEARCH_MCP_MINERU_API_KEY",
            "env_file_path": str(env_file_path()),
            "message": "MinerU API key is configured.",
        }
    return {
        **_mineru_key_setup_prompt(
            "missing",
            "MinerU API key is not configured. Enter it to enable official extract parsing.",
        ),
        "configured": False,
    }


@mcp.tool()
async def search_papers_with_elicitation(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "",
    year: Optional[str] = None,
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Search papers, ask the MCP client for a multi-select choice, then parse.

    MCP clients with elicitation support, such as VS Code Copilot Agent Mode,
    can render the returned schema as a native multi-select control. Clients
    without elicitation support receive the same backend session and numbered
    paper list used by search_papers_for_parsing/parse_selected_papers.
    """
    session_result = await search_papers_for_parsing(
        query=query,
        max_results_per_source=max_results_per_source,
        sources=sources,
        year=year,
    )
    candidates = session_result.get("papers", [])
    if not isinstance(candidates, list):
        candidates = []

    selectable = [candidate for candidate in candidates if candidate.get("parse_ready")]
    if not selectable:
        return {
            **session_result,
            "status": "no_parse_ready_papers",
            "interaction": "backend_session_numbered_selection",
            "message": "No parse-ready papers were found. Use the returned session for inspection or search again.",
        }

    if ctx is None:
        return {
            **session_result,
            "status": "elicitation_unavailable",
            "interaction": "backend_session_numbered_selection",
            "message": "MCP context was not available, so no elicitation request could be sent.",
        }

    options = [_elicitation_option_label(candidate) for candidate in selectable]
    schema = _build_paper_selection_schema(options)

    try:
        elicitation = await ctx.elicit(
            message=(
                "Select papers for MinerU PDF parsing. "
                "If the client does not show a checkbox or multi-select UI, "
                "use the returned selection_token with numbered indices."
            ),
            schema=schema,
        )
    except Exception as exc:
        return {
            **session_result,
            "status": "elicitation_unavailable",
            "interaction": "backend_session_numbered_selection",
            "message": f"Elicitation request failed: {exc}",
        }

    if getattr(elicitation, "action", "") != "accept":
        return {
            **session_result,
            "status": "elicitation_not_accepted",
            "interaction": "backend_session_numbered_selection",
            "elicitation_action": getattr(elicitation, "action", ""),
            "message": "User declined or cancelled the selection. Use parse_selected_papers with numbered indices if needed.",
        }

    selected_values = getattr(getattr(elicitation, "data", None), "selected_papers", [])
    try:
        selected_indices = _parse_elicitation_selected_indices(selected_values, len(candidates))
    except ValueError as exc:
        return {
            **session_result,
            "status": "invalid_elicitation_selection",
            "interaction": "backend_session_numbered_selection",
            "message": str(exc),
        }

    if not selected_indices:
        return {
            **session_result,
            "status": "no_selection",
            "interaction": "backend_session_numbered_selection",
            "message": "No papers were selected. Use parse_selected_papers with numbered indices if needed.",
        }

    parse_result = await parse_selected_papers(
        selection_token=session_result["selection_token"],
        selected_indices=",".join(str(index) for index in selected_indices),
        save_path=save_path,
        use_scihub=use_scihub,
        mode=mode,
        backend=backend,
        force=force,
    )
    return {
        **parse_result,
        "interaction": "elicitation",
        "selection_token": session_result["selection_token"],
        "selected_indices": selected_indices,
        "search": {
            "query": query,
            "sources_requested": sources,
            "sources_used": session_result.get("sources_used", []),
            "source_results": session_result.get("source_results", {}),
            "errors": session_result.get("errors", {}),
            "total": session_result.get("total", 0),
            "parse_ready_total": session_result.get("parse_ready_total", 0),
        },
    }


@mcp.tool()
async def parse_selected_papers(
    selection_token: str,
    selected_indices: str = "all",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Parse papers from a saved search session by numbered selection.

    selected_indices accepts "all", comma-separated values such as "1,3,5",
    or ranges such as "2-4". Sci-Hub remains opt-in via use_scihub.
    """
    invalid_save_path = _invalid_mcp_save_path(save_path)
    if invalid_save_path:
        return invalid_save_path

    save_path = resolve_save_path(save_path)
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    if not session:
        return {
            "status": "not_found",
            "selection_token": selection_token,
            "message": "Search session not found. Run search_papers_for_parsing again.",
        }

    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []

    try:
        indices = _parse_selected_indices(selected_indices, len(papers))
    except ValueError as exc:
        return {
            "status": "invalid_selection",
            "selection_token": selection_token,
            "message": str(exc),
            "total": len(papers),
        }

    async def _run_selected(index: int) -> Dict[str, Any]:
        paper = papers[index - 1]
        if not isinstance(paper, dict):
            return {
                "index": index,
                "status": "skipped",
                "message": "Stored search result is not a paper dictionary.",
            }
        return await _download_and_parse_session_paper(
            paper=paper,
            index=index,
            save_path=save_path,
            use_scihub=use_scihub,
            mode=mode,
            backend=backend,
            force=force,
        )

    concurrency = _env_int(PARSE_CONCURRENCY_ENV, 3, minimum=1)
    semaphore = asyncio.Semaphore(concurrency)

    async def _limited(index: int) -> Dict[str, Any]:
        async with semaphore:
            return await _run_selected(index)

    results = await asyncio.gather(*[_limited(index) for index in indices])

    parsed = sum(1 for result in results if result.get("status") in {"ok", "cached"})
    skipped = sum(1 for result in results if result.get("status") == "skipped")
    failed = len(results) - parsed - skipped
    status = "ok" if failed == 0 else "partial" if parsed else "failed"

    summary = {
        "status": status,
        "selection_token": selection_token,
        "query": session.get("query", ""),
        "selected_indices": indices,
        "results": results,
        "total": len(results),
        "parsed": parsed,
        "failed": failed,
        "skipped": skipped,
        "parse_concurrency": concurrency,
    }
    prompt = _first_mineru_key_prompt(results)
    if prompt:
        summary["mineru_api_key_prompt"] = prompt
    return summary


@mcp.tool()
async def list_search_sessions() -> Dict[str, Any]:
    """List saved search-result selection sessions."""
    sessions = await asyncio.to_thread(cache_list_search_sessions)
    return {"sessions": sessions, "total": len(sessions)}


@mcp.tool()
async def get_search_session(selection_token: str) -> Dict[str, Any]:
    """Return one saved search session as numbered parse candidates."""
    session = await asyncio.to_thread(cache_get_search_session, selection_token)
    if not session:
        return {"status": "not_found", "selection_token": selection_token}

    papers = session.get("papers", [])
    if not isinstance(papers, list):
        papers = []
    return {
        "status": "ok",
        "selection_token": session.get("selection_token", selection_token),
        "query": session.get("query", ""),
        "sources": session.get("sources", ""),
        "created_at": session.get("created_at", ""),
        "metadata": session.get("metadata", {}),
        "papers": [_paper_parse_candidate(paper, index + 1) for index, paper in enumerate(papers) if isinstance(paper, dict)],
        "total": len(papers),
    }


@mcp.tool()
async def delete_search_session(selection_token: str) -> Dict[str, Any]:
    """Delete one saved search-result selection session."""
    deleted = await asyncio.to_thread(cache_delete_search_session, selection_token)
    return {"selection_token": selection_token, "deleted": deleted}


@mcp.tool()
async def list_sources(include_capabilities: bool = True) -> Dict[str, Any]:
    """List configured academic sources and their search/download/read capabilities."""
    sources = []
    for source in ALL_SOURCES:
        entry: Dict[str, Any] = {"name": source}
        if include_capabilities:
            entry.update(SOURCE_CAPABILITIES.get(source, {}))
        sources.append(entry)
    return {"sources": sources, "total": len(sources)}


# Tool definitions
@mcp.tool()
async def search_arxiv(query: str, max_results: int = 10, sort_by: str = 'relevance', sort_order: str = 'descending') -> List[Dict]:
    """Search academic papers from arXiv.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
        sort_by: Sort criterion 鈥?'relevance', 'submittedDate', or 'lastUpdatedDate' (default: 'relevance').
        sort_order: Sort direction 鈥?'descending' or 'ascending' (default: 'descending').
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(arxiv_searcher, query, max_results, sort_by=sort_by, sort_order=sort_order)
    return papers if papers else []


@mcp.tool()
async def search_pubmed(query: str, max_results: int = 10, sort: str = 'relevance') -> List[Dict]:
    """Search academic papers from PubMed.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
        sort: Sort order 鈥?'relevance' or 'pub_date' (default: 'relevance').
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(pubmed_searcher, query, max_results, sort=sort)
    return papers if papers else []


@mcp.tool()
async def search_biorxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from bioRxiv.

    Note: bioRxiv API filters by category name within the last 30 days, not full-text
    keyword search. Use a category keyword such as 'bioinformatics', 'neuroscience',
    'cell biology', etc.

    Args:
        query: Category name to filter by (e.g., 'bioinformatics', 'neuroscience').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(biorxiv_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_medrxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from medRxiv.

    Note: medRxiv API filters by category name within the last 30 days, not full-text
    keyword search. Use a category keyword such as 'infectious_diseases',
    'cardiovascular_medicine', 'oncology', etc.

    Args:
        query: Category name to filter by (e.g., 'infectious_diseases', 'oncology').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(medrxiv_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_google_scholar(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Google Scholar.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(google_scholar_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_iacr(
    query: str, max_results: int = 10, fetch_details: bool = True
) -> List[Dict]:
    """Search academic papers from IACR ePrint Archive.

    Args:
        query: Search query string (e.g., 'cryptography', 'secret sharing').
        max_results: Maximum number of papers to return (default: 10).
        fetch_details: Whether to fetch detailed information for each paper (default: True).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await asyncio.to_thread(iacr_searcher.search, query, max_results, fetch_details)
    return [paper.to_dict() for paper in papers] if papers else []


@mcp.tool()
async def download_arxiv(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF of an arXiv paper.

    Args:
        paper_id: arXiv paper ID (e.g., '2106.12345').
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        Path to the downloaded PDF file.
    """
    return await _download_source_pdf(
        arxiv_searcher,
        source="arxiv",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_pubmed(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Attempt to download PDF of a PubMed paper.

    Args:
        paper_id: PubMed ID (PMID).
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Message indicating that direct PDF download is not supported.
    """
    try:
        return await _download_source_pdf(
            pubmed_searcher,
            source="pubmed",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )
    except NotImplementedError as e:
        return str(e)


@mcp.tool()
async def download_biorxiv(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF of a bioRxiv paper.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        Path to the downloaded PDF file.
    """
    return await _download_source_pdf(
        biorxiv_searcher,
        source="biorxiv",
        paper_id=paper_id,
        doi=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_medrxiv(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF of a medRxiv paper.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        Path to the downloaded PDF file.
    """
    return await _download_source_pdf(
        medrxiv_searcher,
        source="medrxiv",
        paper_id=paper_id,
        doi=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_iacr(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF of an IACR ePrint paper.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        Path to the downloaded PDF file.
    """
    return await _download_source_pdf(
        iacr_searcher,
        source="iacr",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_arxiv_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from an arXiv paper PDF.

    Args:
        paper_id: arXiv paper ID (e.g., '2106.12345').
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: The extracted text content of the paper.
    """
    return await _read_source_paper(
        arxiv_searcher,
        source="arxiv",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_pubmed_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a PubMed paper.

    Args:
        paper_id: PubMed ID (PMID).
        save_path: Directory where the PDF would be saved (unused).
    Returns:
        str: Message indicating that direct paper reading is not supported.
    """
    return await _read_source_paper(
        pubmed_searcher,
        source="pubmed",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_biorxiv_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a bioRxiv paper PDF.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: The extracted text content of the paper.
    """
    return await _read_source_paper(
        biorxiv_searcher,
        source="biorxiv",
        paper_id=paper_id,
        doi=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_medrxiv_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a medRxiv paper PDF.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: The extracted text content of the paper.
    """
    return await _read_source_paper(
        medrxiv_searcher,
        source="medrxiv",
        paper_id=paper_id,
        doi=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_iacr_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from an IACR ePrint paper PDF.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: The extracted text content of the paper.
    """
    return await _read_source_paper(
        iacr_searcher,
        source="iacr",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def search_semantic(query: str, year: Optional[str] = None, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Semantic Scholar.

    Args:
        query: Search query string (e.g., 'machine learning').
        year: Optional year filter (e.g., '2019', '2016-2020', '2010-', '-2015').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    kwargs = {}
    if year is not None:
        kwargs['year'] = year
    papers = await async_search(semantic_searcher, query, max_results, **kwargs)
    return papers if papers else []


@mcp.tool()
async def download_semantic(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF of a Semantic Scholar paper.    

    Args:
        paper_id: Semantic Scholar paper ID, Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        Path to the downloaded PDF file.
    """ 
    return await _download_source_pdf(
        semantic_searcher,
        source="semantic",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_semantic_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a Semantic Scholar paper. 

    Args:
        paper_id: Semantic Scholar paper ID, Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: The extracted text content of the paper.
    """
    return await _read_source_paper(
        semantic_searcher,
        source="semantic",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def search_crossref(
    query: str,
    max_results: int = 10,
    filter: Optional[str] = None,
    sort: Optional[str] = None,
    order: Optional[str] = None,
) -> List[Dict]:
    """Search academic papers from CrossRef database.
    
    CrossRef is a scholarly infrastructure organization that provides 
    persistent identifiers (DOIs) for scholarly content and metadata.
    It's one of the largest citation databases covering millions of 
    academic papers, journals, books, and other scholarly content.

    Args:
        query: Search query string (e.g., 'machine learning', 'climate change').
        max_results: Maximum number of papers to return (default: 10, max: 1000).
        filter: CrossRef filter string (e.g., 'has-full-text:true,from-pub-date:2020').
        sort: Sort field ('relevance', 'published', 'updated', 'deposited', etc.).
        order: Sort order ('asc' or 'desc').
    Returns:
        List of paper metadata in dictionary format.
    """
    extra = {k: v for k, v in {'filter': filter, 'sort': sort, 'order': order}.items() if v is not None}
    papers = await async_search(crossref_searcher, query, max_results, **extra)
    return papers if papers else []


@mcp.tool()
async def get_crossref_paper_by_doi(doi: str) -> Dict:
    """Get a specific paper from CrossRef by its DOI.

    Args:
        doi: Digital Object Identifier (e.g., '10.1038/nature12373').
    Returns:
        Paper metadata in dictionary format, or empty dict if not found.
        
    Example:
        get_crossref_paper_by_doi("10.1038/nature12373")
    """
    paper = await asyncio.to_thread(crossref_searcher.get_paper_by_doi, doi)
    return paper.to_dict() if paper else {}


@mcp.tool()
async def download_crossref(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Attempt to download PDF of a CrossRef paper.

    Args:
        paper_id: CrossRef DOI (e.g., '10.1038/nature12373').
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Message indicating that direct PDF download is not supported.
        
    Note:
        CrossRef is a citation database and doesn't provide direct PDF downloads.
        Use the DOI to access the paper through the publisher's website.
    """
    return await _download_source_pdf(
        crossref_searcher,
        source="crossref",
        paper_id=paper_id,
        doi=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_scihub(
    identifier: str,
    save_path: str = DEFAULT_SAVE_PATH,
    base_url: str = "https://sci-hub.se",
    ctx: Optional[Context] = None,
) -> Any:
    """Download paper PDF via Sci-Hub (optional fallback connector).

    Args:
        identifier: DOI, title, PMID, or paper URL.
        save_path: Directory to save the PDF.
        base_url: Sci-Hub mirror URL.
    Returns:
        Downloaded PDF path on success; error message on failure.
    """
    invalid_save_path = _invalid_mcp_save_path(save_path)
    if invalid_save_path:
        return invalid_save_path

    save_path = resolve_save_path(save_path)
    fetcher = SciHubFetcher(base_url=base_url, output_dir=save_path)
    result = await asyncio.to_thread(fetcher.download_pdf, identifier)
    if result:
        return await _after_saved_pdf(
            result,
            source="scihub",
            paper_id=identifier,
            doi=extract_doi(identifier),
            title=identifier,
            save_path=save_path,
            downloader="scihub",
            legal_status="user_opt_in_scihub",
            ctx=ctx,
        )
    return "Sci-Hub download failed. Try DOI first, then title, or change mirror URL."


@mcp.tool()
async def download_pmc(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download a PubMed Central open-access PDF."""
    return await _download_source_pdf(
        pmc_searcher,
        source="pmc",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_pmc_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download and extract text from a PubMed Central open-access PDF."""
    return await _read_source_paper(
        pmc_searcher,
        source="pmc",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_core(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download a CORE PDF when the record has an accessible PDF URL."""
    return await _download_source_pdf(
        core_searcher,
        source="core",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_core_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download and extract text from a CORE PDF when available."""
    return await _read_source_paper(
        core_searcher,
        source="core",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_europepmc(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download a Europe PMC open-access PDF when available."""
    return await _download_source_pdf(
        europepmc_searcher,
        source="europepmc",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_europepmc_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download and extract text from a Europe PMC open-access PDF when available."""
    return await _read_source_paper(
        europepmc_searcher,
        source="europepmc",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


async def _download_with_fallback_path(
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    scihub_base_url: str = "https://sci-hub.se",
) -> str:
    """Try source-native download, OA repositories, Unpaywall, then opt-in Sci-Hub.

    Args:
        source: Source name (arxiv, biorxiv, medrxiv, iacr, semantic, crossref, pubmed, pmc, core, europepmc, citeseerx, doaj, base, zenodo, hal, ssrn).
        paper_id: Source-native paper identifier.
        doi: Optional DOI used for repository/unpaywall/Sci-Hub fallback.
        title: Optional title used for repository/Sci-Hub fallback when DOI is unavailable.
        save_path: Directory to save downloaded files.
        use_scihub: Whether to fallback to Sci-Hub after OA attempts fail. Defaults to False.
        scihub_base_url: Sci-Hub mirror URL for fallback.
    Returns:
        Download path on success or explanatory error message.
    """
    save_path = resolve_save_path(save_path)
    source_name = source.strip().lower()

    oa_result, attempt_errors = await _race_oa_downloads(
        source_name=source_name,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
    )
    if oa_result and isinstance(oa_result.get("path"), str):
        return oa_result["path"]

    if not use_scihub:
        return "Download failed after OA fallback chain. Details: " + " | ".join(attempt_errors)

    fallback_identifier = (doi or "").strip() or (title or "").strip() or paper_id
    fetcher = SciHubFetcher(base_url=scihub_base_url, output_dir=save_path)
    fallback_result = await asyncio.to_thread(fetcher.download_pdf, fallback_identifier)
    if fallback_result:
        if os.path.exists(fallback_result):
            record_download(
                pdf_path=fallback_result,
                source=source_name,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader="scihub",
                legal_status="user_opt_in_scihub",
            )
        return fallback_result

    return "Download failed after OA fallback chain and Sci-Hub fallback. Details: " + " | ".join(attempt_errors)


@mcp.tool()
async def download_with_fallback(
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    scihub_base_url: str = "https://sci-hub.se",
    ctx: Optional[Context] = None,
) -> Any:
    """Try source-native/OA fallback download, then ask whether to parse saved PDFs."""
    invalid_save_path = _invalid_mcp_save_path(save_path)
    if invalid_save_path:
        return invalid_save_path

    result = await _download_with_fallback_path(
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
        use_scihub=use_scihub,
        scihub_base_url=scihub_base_url,
    )
    legal_status = "source_native_or_open_access"
    if isinstance(result, str) and os.path.exists(result):
        if use_scihub and "sci" in Path(result).name.lower():
            legal_status = "user_opt_in_scihub"
        return await _after_saved_pdf(
            result,
            source=source.strip().lower(),
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            downloader="download_with_fallback",
            legal_status=legal_status,
            ctx=ctx,
        )
    return result


async def _download_and_parse_session_paper(
    paper: Dict[str, Any],
    index: int,
    save_path: str,
    use_scihub: bool,
    mode: str,
    backend: str,
    force: bool,
) -> Dict[str, Any]:
    candidate = _paper_parse_candidate(paper, index)
    if not candidate["parse_ready"]:
        return {
            "index": index,
            "status": "skipped",
            "candidate": candidate,
            "message": candidate["reason"],
        }

    source = candidate["source"]
    paper_id = candidate["paper_id"]
    doi = candidate["doi"]
    title = candidate["title"]
    pdf_url = candidate["pdf_url"]
    local_pdf_path = candidate.get("local_pdf_path", "")
    download_id = paper_id or doi or title

    if local_pdf_path and os.path.exists(local_pdf_path):
        parse_result = await parse_pdf_with_mineru(
            pdf_path=local_pdf_path,
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            mode=mode,
            backend=backend,
            force=force,
        )
        return {
            "index": index,
            "status": parse_result.get("status", "unknown"),
            "candidate": candidate,
            "download_method": "local_pdf_path",
            "pdf_path": local_pdf_path,
            "parse": parse_result,
        }

    direct_error = ""
    if pdf_url:
        filename_hint = f"{source}_{download_id or index}"
        direct_path = await _download_from_url(pdf_url, save_path, filename_hint)
        if isinstance(direct_path, str) and os.path.exists(direct_path):
            record_download(
                pdf_path=direct_path,
                source=source,
                paper_id=paper_id,
                doi=doi,
                title=title,
                downloader="search_result_pdf_url",
                legal_status="search_result_open_access_pdf_url",
            )
            parse_result = await parse_pdf_with_mineru(
                pdf_path=direct_path,
                source=source,
                paper_id=paper_id,
                doi=doi,
                title=title,
                mode=mode,
                backend=backend,
                force=force,
            )
            return {
                "index": index,
                "status": parse_result.get("status", "unknown"),
                "candidate": candidate,
                "download_method": "search_result_pdf_url",
                "pdf_path": direct_path,
                "parse": parse_result,
            }
        direct_error = "direct pdf_url download failed"

    pdf_path = await _download_with_fallback_path(
        source=source,
        paper_id=download_id,
        doi=doi,
        title=title,
        save_path=save_path,
        use_scihub=use_scihub,
    )
    if not isinstance(pdf_path, str) or not os.path.exists(pdf_path):
        message = str(pdf_path)
        if direct_error:
            message = f"{direct_error}; {message}"
        return {
            "index": index,
            "status": "download_failed",
            "candidate": candidate,
            "message": message,
        }

    parse_result = await parse_pdf_with_mineru(
        pdf_path=pdf_path,
        source=source,
        paper_id=paper_id or doi,
        doi=doi,
        title=title,
        mode=mode,
        backend=backend,
        force=force,
    )
    return {
        "index": index,
        "status": parse_result.get("status", "unknown"),
        "candidate": candidate,
        "download_method": "download_with_fallback",
        "pdf_path": pdf_path,
        "parse": parse_result,
    }


@mcp.tool()
async def parse_pdf_with_mineru(
    pdf_path: str,
    paper_key: str = "",
    source: str = "",
    paper_id: str = "",
    doi: str = "",
    title: str = "",
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Parse a local PDF into cached Markdown, content_list JSON, manifest, and assets.

    In auto mode, the parser uses the configured MinerU API key first and only
    falls back to pypdf if official extract parsing is unavailable.
    """
    result = await asyncio.to_thread(
        run_parse_pdf_with_mineru,
        pdf_path,
        paper_key_hint=paper_key,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        mode=mode,
        backend=backend,
        force=force,
    )
    return _attach_mineru_key_prompt(result)


@mcp.tool()
async def parse_downloaded_paper(
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    save_path: str = DEFAULT_SAVE_PATH,
    use_scihub: bool = False,
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Download a paper using the legal-first fallback chain, then parse the PDF."""
    invalid_save_path = _invalid_mcp_save_path(save_path)
    if invalid_save_path:
        return invalid_save_path

    pdf_path = await _download_with_fallback_path(
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        save_path=save_path,
        use_scihub=use_scihub,
    )
    if not isinstance(pdf_path, str) or not os.path.exists(pdf_path):
        return {
            "status": "download_failed",
            "source": source,
            "paper_id": paper_id,
            "doi": doi,
            "title": title,
            "message": pdf_path,
        }

    parse_result = await parse_pdf_with_mineru(
        pdf_path=pdf_path,
        source=source,
        paper_id=paper_id,
        doi=doi,
        title=title,
        mode=mode,
        backend=backend,
        force=force,
    )
    result = {"status": parse_result.get("status", "ok"), "pdf_path": pdf_path, "parse": parse_result}
    prompt = _first_mineru_key_prompt(parse_result)
    if prompt:
        result["mineru_api_key_prompt"] = prompt
    return result


@mcp.tool()
async def mineru_health_check(mode: str = "auto", backend: str = "") -> Dict[str, Any]:
    """Check MinerU API key setup and pypdf fallback status.

    Local API and CLI are checked only when explicitly requested via mode.
    """
    result = await asyncio.to_thread(run_mineru_health_check, mode=mode, backend=backend)
    extract_api = result.get("extract_api", {}) if isinstance(result, dict) else {}
    if not extract_api.get("ok"):
        result = {
            **result,
            "mineru_api_key_prompt": _mineru_key_setup_prompt(
                "missing",
                "MinerU API key is not configured. Enter it to enable official extract parsing.",
            ),
        }
    return result


@mcp.tool()
async def list_parsed_papers() -> Dict[str, Any]:
    """List cached parsed papers."""
    entries = await asyncio.to_thread(list_parsed)
    return {"papers": entries, "total": len(entries)}


@mcp.tool()
async def get_parsed_paper(paper_key: str, output_format: str = "markdown") -> Any:
    """Read cached parsed paper data as markdown, json, manifest, metadata, or paths."""
    return await asyncio.to_thread(read_parsed, paper_key, output_format)


@mcp.tool()
async def get_paper_assets(paper_key: str, asset_type: str = "all") -> List[Dict[str, str]]:
    """List cached extracted assets for a parsed paper."""
    return await asyncio.to_thread(list_assets, paper_key, asset_type)


@mcp.tool()
async def search_parsed_paper(paper_key: str, query: str, max_results: int = 20) -> Dict[str, Any]:
    """Search cached parsed Markdown/content blocks for a query string."""
    hits = await asyncio.to_thread(search_parsed, paper_key, query, max_results)
    return {"paper_key": paper_key, "query": query, "hits": hits, "total": len(hits)}


@mcp.tool()
async def delete_parsed_cache(paper_key: str) -> Dict[str, Any]:
    """Delete cached parsed artifacts for one paper."""
    deleted = await asyncio.to_thread(delete_cache, paper_key)
    return {"paper_key": paper_key, "deleted": deleted}


@mcp.tool()
async def cleanup_redundant_cache_artifacts(apply: bool = False) -> Dict[str, Any]:
    """Remove historical heavyweight cache duplicates; dry-run unless apply is true."""
    return await asyncio.to_thread(cleanup_redundant_artifacts, None, dry_run=not apply)


@mcp.tool()
async def get_parsed_paths(paper_key: str) -> Dict[str, str]:
    """Return filesystem paths for metadata plus resolved Markdown, JSON, and assets."""
    return await asyncio.to_thread(resolved_parsed_paths, paper_key)


@mcp.tool()
async def read_crossref_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Attempt to read and extract text content from a CrossRef paper.

    Args:
        paper_id: CrossRef DOI (e.g., '10.1038/nature12373').
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Message indicating that direct paper reading is not supported.
        
    Note:
        CrossRef is a citation database and doesn't provide direct paper content.
        Use the DOI to access the paper through the publisher's website.
    """
    return await _read_source_paper(
        crossref_searcher,
        source="crossref",
        paper_id=paper_id,
        doi=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def search_openalex(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from OpenAlex.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(openalex_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_pmc(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from PubMed Central (PMC).

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(pmc_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_core(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from CORE.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(core_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_europepmc(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Europe PMC.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(europepmc_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_dblp(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from dblp computer science bibliography.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(dblp_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_openaire(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from OpenAIRE European Open Access infrastructure.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(openaire_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_citeseerx(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from CiteSeerX digital library.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(citeseerx_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_doaj(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from DOAJ (Directory of Open Access Journals).

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(doaj_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_base(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from BASE (Bielefeld Academic Search Engine).

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(base_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_zenodo(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Zenodo open repository.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(zenodo_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_hal(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from HAL open archive.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(hal_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_ssrn(query: str, max_results: int = 10) -> List[Dict]:
    """Search metadata records from SSRN.

    Note: SSRN connector is metadata-only and does not support direct PDF download.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(ssrn_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_unpaywall(query: str, max_results: int = 10) -> List[Dict]:
    """Lookup a DOI via Unpaywall and return OA metadata.

    Unpaywall is DOI-centric and does not support generic keyword search.
    This tool extracts the first DOI from `query` and returns at most one record.

    Args:
        query: DOI string or text containing a DOI.
        max_results: Kept for API consistency; Unpaywall returns max 1 record.
    Returns:
        List with one paper metadata dict when DOI is resolvable, else empty list.
    """
    papers = await async_search(unpaywall_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def read_dblp_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Attempt to read and extract text content from a dblp paper.

    Note: dblp doesn't provide direct paper content access.
    This function returns an informative message.

    Args:
        paper_id: dblp paper identifier.
        save_path: Directory where the PDF would be saved (unused).
    Returns:
        str: Message indicating that direct paper reading is not supported.
    """
    return await _read_source_paper(
        dblp_searcher,
        source="dblp",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_dblp(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from dblp.

    Note: dblp doesn't provide direct PDF access.
    This function returns an informative message.

    Args:
        paper_id: dblp paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Message indicating that direct PDF download is not supported.
    """
    return await _download_source_pdf(
        dblp_searcher,
        source="dblp",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_openaire_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Attempt to read and extract text content from an OpenAIRE paper.

    Args:
        paper_id: OpenAIRE paper identifier.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Extracted text or error message.
    """
    return await _read_source_paper(
        openaire_searcher,
        source="openaire",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_openaire(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from OpenAIRE.

    Args:
        paper_id: OpenAIRE paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Path to downloaded PDF or error message.
    """
    return await _download_source_pdf(
        openaire_searcher,
        source="openaire",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_citeseerx_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a CiteSeerX paper.

    Args:
        paper_id: CiteSeerX paper identifier.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Extracted text or fallback abstract/error message.
    """
    return await _read_source_paper(
        citeseerx_searcher,
        source="citeseerx",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_citeseerx(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from CiteSeerX.

    Args:
        paper_id: CiteSeerX paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Path to downloaded PDF or error message.
    """
    return await _download_source_pdf(
        citeseerx_searcher,
        source="citeseerx",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_doaj_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a DOAJ paper.

    Args:
        paper_id: DOAJ paper identifier.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Extracted text content.
    """
    return await _read_source_paper(
        doaj_searcher,
        source="doaj",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_doaj(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from DOAJ.

    Args:
        paper_id: DOAJ paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Path to downloaded PDF.
    """
    return await _download_source_pdf(
        doaj_searcher,
        source="doaj",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_base_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a BASE paper.

    Args:
        paper_id: BASE paper identifier.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Extracted text content.
    """
    return await _read_source_paper(
        base_searcher,
        source="base",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_base(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from BASE.

    Args:
        paper_id: BASE paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Path to downloaded PDF.
    """
    return await _download_source_pdf(
        base_searcher,
        source="base",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_zenodo_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a Zenodo paper.

    Args:
        paper_id: Zenodo paper identifier.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Extracted text content.
    """
    return await _read_source_paper(
        zenodo_searcher,
        source="zenodo",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_zenodo(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from Zenodo.

    Args:
        paper_id: Zenodo paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Path to downloaded PDF.
    """
    return await _download_source_pdf(
        zenodo_searcher,
        source="zenodo",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_hal_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read and extract text content from a HAL paper.

    Args:
        paper_id: HAL paper identifier.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Extracted text content.
    """
    return await _read_source_paper(
        hal_searcher,
        source="hal",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_hal(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from HAL.

    Args:
        paper_id: HAL paper identifier.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Path to downloaded PDF.
    """
    return await _download_source_pdf(
        hal_searcher,
        source="hal",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_ssrn_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Read paper content from SSRN.

    Note: SSRN connector is metadata-only and read is not supported.

    Args:
        paper_id: SSRN paper identifier.
        save_path: Directory where the PDF is/will be saved (unused).
    Returns:
        str: Error message from metadata-only SSRN connector.
    """
    return await _read_source_paper(
        ssrn_searcher,
        source="ssrn",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_ssrn(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from SSRN.

    Note: SSRN connector is metadata-only and download is not supported.

    Args:
        paper_id: SSRN paper identifier.
        save_path: Directory to save the PDF (unused).
    Returns:
        str: Error message from metadata-only SSRN connector.
    """
    return await _download_source_pdf(
        ssrn_searcher,
        source="ssrn",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def read_openalex_paper(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Attempt to read and extract text content from an OpenAlex paper.

    Args:
        paper_id: OpenAlex paper ID.
        save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
    Returns:
        str: Message indicating that direct paper reading is not supported natively.
    """
    return await _read_source_paper(
        openalex_searcher,
        source="openalex",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


@mcp.tool()
async def download_openalex(
    paper_id: str,
    save_path: str = DEFAULT_SAVE_PATH,
    ctx: Optional[Context] = None,
) -> Any:
    """Download PDF for a paper from OpenAlex.

    Args:
        paper_id: OpenAlex paper ID.
        save_path: Directory to save the PDF (default: '~/Desktop/papers').
    Returns:
        str: Error message, typically OpenAlex relies on extracted pdf_url instead of direct downloads.
    """
    return await _download_source_pdf(
        openalex_searcher,
        source="openalex",
        paper_id=paper_id,
        save_path=save_path,
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# Optional IEEE Xplore tools 鈥?registered only when API key is set
# ---------------------------------------------------------------------------
if ieee_searcher is not None:
    @mcp.tool()
    async def search_ieee(query: str, max_results: int = 10) -> List[Dict]:
        """Search IEEE Xplore for papers.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY).

        Args:
            query: Search query string.
            max_results: Maximum number of results (default: 10).
        Returns:
            List of paper dicts from IEEE Xplore.
        """
        return await async_search(ieee_searcher, query, max_results)

    @mcp.tool()
    async def download_ieee(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download a PDF from IEEE Xplore.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY) and institutional access.

        Args:
            paper_id: IEEE Xplore paper identifier.
            save_path: Directory to save the PDF (default: '~/Desktop/papers').
        Returns:
            str: Path to saved PDF or error message.
        """
        return await _download_source_pdf(
            ieee_searcher,
            source="ieee",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )

    @mcp.tool()
    async def read_ieee_paper(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download and read an IEEE Xplore paper.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY).

        Args:
            paper_id: IEEE Xplore paper identifier.
            save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
        Returns:
            str: Extracted text content.
        """
        return await _read_source_paper(
            ieee_searcher,
            source="ieee",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )


# ---------------------------------------------------------------------------
# Optional ACM Digital Library tools 鈥?registered only when API key is set
# ---------------------------------------------------------------------------
if acm_searcher is not None:
    @mcp.tool()
    async def search_acm(query: str, max_results: int = 10) -> List[Dict]:
        """Search ACM Digital Library for papers.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY).

        Args:
            query: Search query string.
            max_results: Maximum number of results (default: 10).
        Returns:
            List of paper dicts from ACM DL.
        """
        return await async_search(acm_searcher, query, max_results)

    @mcp.tool()
    async def download_acm(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download a PDF from ACM Digital Library.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY) and institutional access.

        Args:
            paper_id: ACM DL paper identifier.
            save_path: Directory to save the PDF (default: '~/Desktop/papers').
        Returns:
            str: Path to saved PDF or error message.
        """
        return await _download_source_pdf(
            acm_searcher,
            source="acm",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )

    @mcp.tool()
    async def read_acm_paper(
        paper_id: str,
        save_path: str = DEFAULT_SAVE_PATH,
        ctx: Optional[Context] = None,
    ) -> Any:
        """Download and read an ACM Digital Library paper.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY).

        Args:
            paper_id: ACM DL paper identifier.
            save_path: Directory where the PDF is/will be saved (default: '~/Desktop/papers').
        Returns:
            str: Extracted text content.
        """
        return await _read_source_paper(
            acm_searcher,
            source="acm",
            paper_id=paper_id,
            save_path=save_path,
            ctx=ctx,
        )


def main():
    transport = get_env("TRANSPORT", "stdio").strip().lower() or "stdio"
    if transport in {"http", "streamable_http", "streamable-http"}:
        _configure_http_transport_from_env()
        logger.info(
            "Starting paper-search MCP over streamable HTTP at http://%s:%s%s",
            mcp.settings.host,
            mcp.settings.port,
            mcp.settings.streamable_http_path,
        )
        mcp.run(transport="streamable-http")
        return
    if transport == "sse":
        _configure_http_transport_from_env()
        logger.info("Starting paper-search MCP over SSE at http://%s:%s/sse", mcp.settings.host, mcp.settings.port)
        mcp.run(transport="sse")
        return
    if transport != "stdio":
        logger.warning("Unknown PAPER_SEARCH_MCP_TRANSPORT=%r; falling back to stdio", transport)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

