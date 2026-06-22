# paper_search_mcp/tools/parse.py
"""MinerU PDF parse tool registration.

Extracted from server.py.  Registers parse-related MCP tools:
- parse_pdf_with_mineru
- parse_pdfs_with_mineru
- parse_downloaded_paper
- mineru_health_check
- mineru_setup_status
- configure_mineru_api_key

Callables that live in server.py (``_download_with_fallback_path``,
``_invalid_mcp_save_path``) are accepted as optional overrides so the
register function does not create a circular import back to server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Callable, Dict, Optional

from ..config import env_file_path, set_env_value
from ..engine.parse import (
    _attach_mineru_key_prompt,
    _first_mineru_key_prompt,
    _mineru_api_key_configured,
    _mineru_batch_parse_enabled,
    _mineru_key_setup_prompt,
)
from ..parsers.mineru import (
    mineru_health_check as run_mineru_health_check,
    parse_pdf_with_mineru as run_parse_pdf_with_mineru,
    parse_pdfs_with_mineru as run_parse_pdfs_with_mineru,
)
from ..utils import DEFAULT_SAVE_PATH

logger = logging.getLogger(__name__)

MINERU_KEY_WIDGET_URI = "ui://paper-search/mineru-api-key.html"
MINERU_KEY_CONFIG_TOOL = "configure_mineru_api_key"


def register_parse_tools(
    mcp,
    *,
    _download_with_fallback_path_fn: Optional[Callable] = None,
    _invalid_mcp_save_path_fn: Optional[Callable] = None,
) -> None:
    """Register MinerU parse-related tools on the FastMCP instance.

    Parameters
    ----------
    mcp : FastMCP
        The MCP server instance to register tools on.
    _download_with_fallback_path_fn : async callable | None
        Override for the server.py ``_download_with_fallback_path`` async
        helper.  When ``None``, the callable is lazy-imported from
        ``..server`` on first invocation (avoids a hard import cycle).
    _invalid_mcp_save_path_fn : callable | None
        Override for the server.py ``_invalid_mcp_save_path`` helper.
        Falls back to the same lazy-import strategy.
    """

    # ------------------------------------------------------------------
    # Lazy-import fallbacks for functions still living in server.py
    # ------------------------------------------------------------------
    async def _lazy_download_with_fallback_path(
        source: str,
        paper_id: str,
        doi: str = "",
        title: str = "",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        scihub_base_url: str = "https://sci-hub.se",
        download_strategy: str = "",
        use_libgen: Optional[bool] = None,
        libgen_base_url: str = "",
    ) -> str:
        fn = _download_with_fallback_path_fn
        if fn is None:
            from ..server import (  # noqa: PLC0415
                _download_with_fallback_path as _fn,
            )
            fn = _fn
        return await fn(
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            use_scihub=use_scihub,
            scihub_base_url=scihub_base_url,
            download_strategy=download_strategy,
            use_libgen=use_libgen,
            libgen_base_url=libgen_base_url,
        )

    def _lazy_invalid_mcp_save_path(
        save_path: str,
        *,
        custom_save_path_confirmed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        fn = _invalid_mcp_save_path_fn
        if fn is None:
            from ..server import (  # noqa: PLC0415
                _invalid_mcp_save_path as _fn,
            )
            fn = _fn
        return fn(
            save_path,
            custom_save_path_confirmed=custom_save_path_confirmed,
        )

    # ------------------------------------------------------------------
    # Tool: configure_mineru_api_key
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Tool: mineru_setup_status
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Tool: parse_pdf_with_mineru
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Tool: parse_pdfs_with_mineru
    # ------------------------------------------------------------------
    @mcp.tool()
    async def parse_pdfs_with_mineru(
        pdf_paths: str,
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Parse multiple local PDFs; newline/comma/semicolon separated paths are accepted."""
        paths = [part.strip() for part in re.split(r"[\n;,]+", pdf_paths or "") if part.strip()]
        if not paths:
            return {
                "status": "invalid_request",
                "message": "At least one PDF path is required.",
                "results": [],
            }
        results = await asyncio.to_thread(
            run_parse_pdfs_with_mineru,
            [{"pdf_path": path} for path in paths],
            mode=mode,
            backend=backend,
            force=force,
        )
        results = [_attach_mineru_key_prompt(result) for result in results]
        parsed = sum(1 for result in results if result.get("status") in {"ok", "cached"})
        failed = len(results) - parsed
        status = "ok" if failed == 0 else "partial" if parsed else "failed"
        response = {
            "status": status,
            "results": results,
            "total": len(results),
            "parsed": parsed,
            "failed": failed,
            "batch_parse": {
                "attempted": len(results) > 1 and _mineru_batch_parse_enabled(mode),
                "mode": mode or "auto",
            },
        }
        prompt = _first_mineru_key_prompt(results)
        if prompt:
            response["mineru_api_key_prompt"] = prompt
        return response

    # ------------------------------------------------------------------
    # Tool: parse_downloaded_paper
    # ------------------------------------------------------------------
    @mcp.tool()
    async def parse_downloaded_paper(
        source: str,
        paper_id: str,
        doi: str = "",
        title: str = "",
        save_path: str = DEFAULT_SAVE_PATH,
        use_scihub: bool = False,
        download_strategy: str = "",
        use_libgen: Optional[bool] = None,
        libgen_base_url: str = "",
        mode: str = "auto",
        backend: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Download a paper using the legal-first fallback chain, then parse the PDF."""
        invalid_save_path = _lazy_invalid_mcp_save_path(save_path)
        if invalid_save_path:
            return invalid_save_path

        pdf_path = await _lazy_download_with_fallback_path(
            source=source,
            paper_id=paper_id,
            doi=doi,
            title=title,
            save_path=save_path,
            use_scihub=use_scihub,
            download_strategy=download_strategy,
            use_libgen=use_libgen,
            libgen_base_url=libgen_base_url,
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
        result = {
            "status": parse_result.get("status", "ok"),
            "pdf_path": pdf_path,
            "parse": parse_result,
        }
        prompt = _first_mineru_key_prompt(parse_result)
        if prompt:
            result["mineru_api_key_prompt"] = prompt
        return result

    # ------------------------------------------------------------------
    # Tool: mineru_health_check
    # ------------------------------------------------------------------
    @mcp.tool()
    async def mineru_health_check(
        mode: str = "auto",
        backend: str = "",
    ) -> Dict[str, Any]:
        """Check MinerU API key setup and pypdf fallback status.

        Local API and CLI are checked only when explicitly requested via mode.
        """
        result = await asyncio.to_thread(
            run_mineru_health_check, mode=mode, backend=backend
        )
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
