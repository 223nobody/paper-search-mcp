# paper_search_mcp/ui/ext_apps_bundle.py
"""Download and cache the @modelcontextprotocol/ext-apps browser bundle.

The bundle is a self-contained ESM module (~300 KB) that the widget iframe
needs for the standard MCP Apps ``App`` class API.  Claude Desktop / claude.ai
do not expose ``window.openai``; instead they expect the widget to call
``App.connect()`` and receive data via ``app.ontoolresult``.

This module:
  1. Downloads the bundle from unpkg CDN on first access
  2. Caches it on disk under ``~/.paper_search_mcp/``
  3. Transforms the final ``export { ... }`` statement into a
     ``globalThis.ExtApps = { ... }`` assignment so it works inside a
     sandboxed (non-module) ``<script>`` tag
  4. Returns the inlined JS snippet when ``get_ext_apps_bundle()`` is called
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_EXT_APPS_CDN_URL = (
    "https://unpkg.com/@modelcontextprotocol/ext-apps@1/dist/src/app-with-deps.js"
)
_CACHE_DIR = Path.home() / ".paper_search_mcp"
_CACHE_FILE = _CACHE_DIR / "ext_apps_bundle.js"
_CACHE_MAX_AGE_SECONDS = 86400 * 7  # re-fetch once per week

_bundle_cache: Optional[str] = None


def _transform_esm_exports(bundle_text: str) -> str:
    """Replace the final ``export { A, B as C }`` with a globalThis assignment.

    The *app-with-deps* bundle is a self-contained ESM file whose very last
    statement exports the public symbols.  We capture those symbols and
    reassign them onto ``globalThis.ExtApps`` so the widget can use them
    without an ESM-compatible loader inside the sandboxed iframe.

    Per Anthropic's official recommendation:
    https://github.com/anthropics/claude-plugins-official/blob/main/plugins/
    mcp-server-dev/skills/build-mcp-app/references/iframe-sandbox.md
    """
    pattern = re.compile(
        r"export\s*\{([^}]+)\}\s*;?\s*$",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(bundle_text)
    if not match:
        logger.warning(
            "ext-apps bundle: could not find terminal export statement; "
            "the widget may not have access to the App class."
        )
        return bundle_text

    exports_body = match.group(1)
    pairs: list[str] = []
    for part in exports_body.split(","):
        part = part.strip()
        if not part:
            continue
        # handles both "App" and "App as defaultApp"
        if " as " in part:
            local, exported = part.rsplit(" as ", 1)
            local = local.strip()
            exported = exported.strip()
        else:
            local = exported = part.strip()
        pairs.append(f"{exported}:{local}")

    global_assignment = "globalThis.ExtApps={" + ",".join(pairs) + "};"
    transformed = pattern.sub(global_assignment, bundle_text)
    logger.info(
        "ext-apps bundle: transformed ESM exports → globalThis.ExtApps "
        "(%d symbols: %s)",
        len(pairs),
        ", ".join(p[0] for p in [p.split(":") for p in pairs]),
    )
    return transformed


def _download_bundle() -> str:
    """Download and cache the ext-apps bundle from CDN."""
    import urllib.request

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Check disk cache ──
    if _CACHE_FILE.exists():
        age = time.time() - _CACHE_FILE.stat().st_mtime
        if age < _CACHE_MAX_AGE_SECONDS:
            cached = _CACHE_FILE.read_text(encoding="utf-8")
            if len(cached) > 1000:  # sanity check
                logger.info(
                    "ext-apps bundle: using disk cache (age=%.0fh)", age / 3600
                )
                return cached

    # ── Download ──
    logger.info("ext-apps bundle: downloading from %s ...", _EXT_APPS_CDN_URL)
    try:
        req = urllib.request.Request(
            _EXT_APPS_CDN_URL,
            headers={"User-Agent": "paper-search-mcp/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning(
            "ext-apps bundle: download failed (%s); widget will fall back "
            "to postMessage / window.openai API.",
            exc,
        )
        # If we have a stale cache, use it as a fallback
        if _CACHE_FILE.exists():
            cached = _CACHE_FILE.read_text(encoding="utf-8")
            if len(cached) > 1000:
                logger.info("ext-apps bundle: using stale disk cache as fallback.")
                return cached
        return ""

    if len(raw) < 1000:
        logger.warning("ext-apps bundle: downloaded content too small; discarding.")
        return ""

    transformed = _transform_esm_exports(raw)

    # ── Write through disk cache ──
    try:
        _CACHE_FILE.write_text(transformed, encoding="utf-8")
    except OSError as exc:
        logger.debug("ext-apps bundle: could not write disk cache: %s", exc)

    return transformed


def get_ext_apps_bundle() -> str:
    """Return the inlined ext-apps JS bundle, downloading it on first access.

    Returns an empty string when the download fails so callers can safely
    inject the result into an HTML template — the widget will fall back to
    the ``window.openai`` or raw postMessage API.
    """
    global _bundle_cache
    if _bundle_cache is None:
        _bundle_cache = _download_bundle()
    return _bundle_cache


def invalidate_bundle_cache() -> None:
    """Clear the in-memory cache so the next call re-reads from disk/CDN.

    Useful for development / testing.
    """
    global _bundle_cache
    _bundle_cache = None
