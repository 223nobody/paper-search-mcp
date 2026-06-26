"""Publisher-version download via scansci-pdf MCP chaining.

Registers tools that connect to the external **scansci-pdf** MCP server to
fetch publisher final versions of arXiv papers already cached in
paper-search-mcp.

scansci-pdf is auto-installed on first use — no manual setup required.
When installation fails (no network, pip issues, ...) the tools return a
clear ``"unavailable"`` status and all other paper-search-mcp functionality
continues to work normally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_env

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# arXiv synthetic DOI prefix — used to distinguish real publisher DOIs from
# the fallback DOIs that arxiv.py synthesises when no publisher DOI is found.
# ---------------------------------------------------------------------------
_ARXIV_DOI_PREFIX = "10.48550"

# ---------------------------------------------------------------------------
# Publisher tool env-configurable timeouts (seconds)
# ---------------------------------------------------------------------------
PUBLISHER_SETUP_TIMEOUT_ENV = "PUBLISHER_SETUP_TIMEOUT_SECONDS"
PUBLISHER_TOR_TIMEOUT_ENV = "PUBLISHER_TOR_TIMEOUT_SECONDS"
PUBLISHER_CLIENT_TIMEOUT_ENV = "PUBLISHER_CLIENT_TIMEOUT_SECONDS"

# ---------------------------------------------------------------------------
# Module-level lazy state for the scansci-pdf MCP client connection
# ---------------------------------------------------------------------------
_scansci_client: Optional[Any] = None  # fastmcp.Client instance
_scansci_error: str = ""
_scansci_setup_done: bool = False  # True after auto_setup + tor_start ran once

# Cache the auto-install result so we only attempt pip install once
_scansci_install_attempted: bool = False
# Track whether scansci-pdf is importable (cached after first check)
_scansci_importable: Optional[bool] = None  # tri-state: None=unchecked


# ---------------------------------------------------------------------------
# Auto-install + component detection
# ---------------------------------------------------------------------------

# Mapping from Python import names → pip install targets for optional
# publisher-download components.  Populated lazily after scansci-pdf is
# importable so we can cross-reference its dependency declarations.
_PUBLISHER_COMPONENT_PIP_TARGETS: Dict[str, str] = {
    # Python import → pip install argument
    "cloakbrowser": "cloakbrowser",
    "socks": "requests[socks]",
    "Crypto": "pycryptodome",
}

# Cached component status — refreshed after each install attempt.
_component_status: Dict[str, bool] = {}


def _check_playwright_browser() -> Dict[str, Any]:
    """Check if Playwright and its Chromium browser are installed for CloakBrowser.

    Returns a dict with keys ``playwright_available``, ``chromium_browser``,
    and ``message``.
    """
    # 1. Is the playwright Python package importable?
    try:
        import playwright  # noqa: F401, PLC0415
    except ImportError:
        return {
            "playwright_available": False,
            "chromium_browser": False,
            "message": "playwright Python package not installed",
        }

    # 2. Is the Chromium browser binary installed?
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = (result.stdout + result.stderr).lower()
        if "chromium" in combined:
            return {
                "playwright_available": True,
                "chromium_browser": False,
                "message": (
                    "Playwright package installed but Chromium browser not "
                    "downloaded. Run: playwright install chromium"
                ),
            }
        return {
            "playwright_available": True,
            "chromium_browser": True,
            "message": "Playwright Chromium browser ready",
        }
    except Exception as exc:
        return {
            "playwright_available": True,
            "chromium_browser": None,
            "message": f"Could not determine browser status: {exc}",
        }


def _detect_publisher_components() -> Dict[str, Dict[str, Any]]:
    """Detect which publisher-download components are available.

    Uses scansci-pdf's own dependency checker when scansci-pdf is
    importable; falls back to direct ``importlib`` checks otherwise.

    Returns a dict keyed by component name, each value containing
    ``available`` (bool) and ``description`` (str).
    """
    global _component_status

    result: Dict[str, Dict[str, Any]] = {}

    # Try scansci-pdf's built-in checker first (most accurate)
    try:
        from scansci_pdf.deps import check_all as _scansci_check_all  # noqa: PLC0415
        scansci_report = _scansci_check_all()
        for module, info in scansci_report.get("optional", {}).items():
            result[module] = {
                "available": bool(info.get("available")),
                "description": str(info.get("description", "")),
            }
    except Exception:
        pass

    # Fill in any components that scansci-pdf didn't report
    import importlib as _importlib
    for mod_name, pip_target in _PUBLISHER_COMPONENT_PIP_TARGETS.items():
        if mod_name not in result:
            try:
                _importlib.import_module(mod_name)
                result[mod_name] = {"available": True, "description": pip_target}
            except ImportError:
                result[mod_name] = {"available": False, "description": pip_target}

    # Update cached status
    _component_status.clear()
    for name, info in result.items():
        _component_status[name] = bool(info.get("available"))

    # ── Playwright browser check (needed by CloakBrowser) ──────────
    if result.get("cloakbrowser", {}).get("available"):
        pw_status = _check_playwright_browser()
        result["playwright"] = {
            "available": bool(pw_status.get("chromium_browser")),
            "description": pw_status.get("message", ""),
            "detail": pw_status,
        }

    return result


def _run_pip_install(package_spec: str, timeout: int = 300) -> bool:
    """Install a package, falling back to ``uv pip`` when the venv lacks pip.

    Returns ``True`` if a subprocess succeeded, ``False`` otherwise.
    """
    # Attempt 1: standard pip
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", package_spec],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return True
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "")
        # If pip itself is missing, try uv as a fallback
        if "No module named pip" in stderr:
            logger.info(
                "pip module not found in venv — retrying with uv pip install %s",
                package_spec,
            )
            try:
                subprocess.run(
                    ["uv", "pip", "install", package_spec],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return True
            except subprocess.CalledProcessError as uv_exc:
                logger.warning(
                    "uv pip install also failed: %s",
                    (uv_exc.stderr or str(uv_exc))[-300:],
                )
                return False
            except Exception as uv_exc:
                logger.warning("uv pip install error: %s", uv_exc)
                return False
        logger.warning("pip install %s failed: %s", package_spec, stderr[-200:])
        return False
    except subprocess.TimeoutExpired:
        logger.warning(
            "pip install %s timed out after %ds.", package_spec, timeout
        )
        return False
    except Exception as exc:
        logger.warning("pip install %s error: %s", exc)
        return False


def _install_missing_components(
    missing: List[str],
    install_timeout: int = 120,
) -> Dict[str, bool]:
    """Attempt to pip-install a list of missing Python packages.

    Returns a dict mapping each package name → whether it is now importable.
    """
    results: Dict[str, bool] = {}
    for mod_name in missing:
        pip_target = _PUBLISHER_COMPONENT_PIP_TARGETS.get(mod_name, mod_name)
        logger.info("Attempting to install %s (pip: %s) …", mod_name, pip_target)
        if _run_pip_install(pip_target, timeout=install_timeout):
            # Verify the module is now importable
            import importlib as _importlib
            try:
                _importlib.import_module(mod_name)
                results[mod_name] = True
                logger.info("  ✓ %s installed and importable.", mod_name)
            except ImportError:
                results[mod_name] = False
                logger.warning("  ✗ %s installed but still not importable.", mod_name)
        else:
            results[mod_name] = False

    # Refresh cached status
    _detect_publisher_components()
    return results


def _auto_install_scansci_pdf() -> bool:
    """Auto-install scansci-pdf AND its optional publisher-access dependencies.

    Phases:
    1. Install scansci-pdf base (fast path: PyPI).
    2. Detect which optional components are still missing.
    3. Auto-install each missing component (CloakBrowser, Tor/SOCKS, Crypto).
    4. Report final status.

    Returns ``True`` if scansci-pdf itself is importable afterwards.
    Missing optional components are NOT fatal — publisher downloads
    gracefully degrade to OA-only sources.
    """
    global _scansci_install_attempted, _scansci_importable, _component_status

    # Fast path: already importable (cached)
    if _scansci_importable is True:
        return True

    # Fast path: check if importable now
    try:
        import scansci_pdf  # noqa: F401
        _scansci_importable = True
        # Still check optional components — they may have been installed
        # manually since the last check.
        _detect_publisher_components()
        return True
    except ImportError:
        pass

    if _scansci_install_attempted:
        # Check again — maybe it was manually installed since last attempt
        try:
            import scansci_pdf  # noqa: F401, PLC0415
            _scansci_importable = True
            _detect_publisher_components()
            return True
        except ImportError:
            _scansci_importable = False
            return False
    _scansci_install_attempted = True

    # ══════════════════════════════════════════════════════════════════
    # Phase 1: Install scansci-pdf base
    # ══════════════════════════════════════════════════════════════════
    logger.info("scansci-pdf not found — attempting auto-install …")
    install_ok = False
    for attempt, install_target in enumerate([
        "scansci-pdf[cloakbrowser]",
        "scansci-pdf",
    ]):
        logger.info(
            "  install %s (attempt %d) …", install_target, attempt + 1
        )
        if _run_pip_install(install_target, timeout=300):
            install_ok = True
            break
        else:
            logger.warning("  %s install failed, trying fallback.", install_target)

    if not install_ok:
        logger.warning("scansci-pdf auto-install failed completely.")
        _scansci_importable = False
        return False

    # Verify scansci-pdf is now importable
    try:
        import scansci_pdf  # noqa: F401, PLC0415
        _scansci_importable = True
        logger.info("scansci-pdf base package installed ✓")
    except ImportError:
        logger.warning("scansci-pdf installed but still not importable")
        _scansci_importable = False
        return False

    # ══════════════════════════════════════════════════════════════════
    # Phase 2: Detect and install missing optional components
    # ══════════════════════════════════════════════════════════════════
    components = _detect_publisher_components()
    missing = [
        name for name, info in components.items()
        if not info.get("available")
    ]

    if missing:
        logger.info(
            "Missing publisher-access components: %s — attempting auto-install …",
            ", ".join(missing),
        )
        install_results = _install_missing_components(missing)

        still_missing = [k for k, v in install_results.items() if not v]
        if still_missing:
            logger.warning(
                "Could not auto-install: %s. "
                "Publisher downloads will use OA-only sources. "
                "Install manually: pip install %s",
                ", ".join(still_missing),
                " ".join(
                    _PUBLISHER_COMPONENT_PIP_TARGETS.get(m, m)
                    for m in still_missing
                ),
            )
        else:
            logger.info("All publisher-access components installed ✓")
    else:
        logger.info("All publisher-access components already available ✓")

    return True


def _publisher_components_summary() -> str:
    """Return a one-line summary of publisher-component availability."""
    components = _detect_publisher_components()
    available = [k for k, v in components.items() if v.get("available")]
    missing = [k for k, v in components.items() if not v.get("available")]
    parts: List[str] = []
    if available:
        parts.append("available: " + ", ".join(available))
    if missing:
        parts.append("missing: " + ", ".join(missing))
    return " | ".join(parts) if parts else "no optional components detected"


def _get_scansci_client() -> Optional[Any]:
    """Return a connected scansci-pdf MCP client, or ``None`` if unavailable.

    * Auto-installs scansci-pdf on first call (if not already present).
    * Retries connection on each call when scansci-pdf is importable but
      the previous connection attempt failed — no permanent failure cache.
    """
    global _scansci_client, _scansci_error, _scansci_importable

    if _scansci_client is not None:
        return _scansci_client

    # Check importability (cached to avoid repeated import attempts)
    if _scansci_importable is None:
        try:
            import scansci_pdf  # noqa: F401, PLC0415
            _scansci_importable = True
        except ImportError:
            _scansci_importable = False

    # ── Ensure scansci-pdf is installed ─────────────────────────
    if not _scansci_importable and not _auto_install_scansci_pdf():
        _scansci_error = (
            "scansci-pdf could not be installed. "
            "Install manually: pip install scansci-pdf[cloakbrowser]  "
            "or: uv pip install scansci-pdf[cloakbrowser]"
        )
        return None

    # ── Create the MCP client via stdio transport ───────────────
    # Allow retry on each call — previous connection failure doesn't
    # permanently disable the feature.
    client_timeout = max(
        30.0,
        float(get_env(PUBLISHER_CLIENT_TIMEOUT_ENV, "120") or "120"),
    )
    try:
        from fastmcp.client import Client  # noqa: PLC0415
        from fastmcp.client.transports import StdioTransport  # noqa: PLC0415

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "scansci_pdf.main", "run"],
            keep_alive=True,
        )
        _scansci_client = Client(transport, timeout=client_timeout)
        _scansci_error = ""
        logger.info(
            "scansci-pdf MCP client initialised (timeout=%ss).", client_timeout
        )
        return _scansci_client

    except (ValueError, FileNotFoundError) as exc:
        _scansci_error = f"scansci-pdf unavailable: {exc}"
        logger.warning(_scansci_error)
        return None
    except ImportError as exc:
        _scansci_error = (
            f"scansci-pdf MCP chaining unavailable (fastmcp not found): {exc}"
        )
        logger.warning(_scansci_error)
        return None
    except Exception as exc:
        _scansci_error = f"scansci-pdf initialisation failed: {exc}"
        logger.warning(_scansci_error)
        return None


async def _ensure_scansci_ready(client: Any) -> Dict[str, Any]:
    """Run one-time environment setup on first scansci-pdf connection.

    Calls ``scansci_pdf_auto_setup`` (auto-starts Tor, probes Sci-Hub
    domains, checks CloakBrowser) and then ``scansci_pdf_tor_start``.

    Each call has a **short timeout** — setup failures are logged but
    never block the download.  ``smart_download`` gracefully degrades
    to OA-only sources when Tor / CloakBrowser are unavailable.

    Subsequent calls are a no-op (``_scansci_setup_done`` flag).
    """
    global _scansci_setup_done

    if _scansci_setup_done:
        return {"setup": "already_done"}

    # ── Timeouts: short by default because Tor/CloakBrowser setup will
    #    inevitably fail when those optional deps are not installed.
    #    When they ARE installed, increase via .env.
    setup_timeout = max(
        3, int(float(get_env(PUBLISHER_SETUP_TIMEOUT_ENV, "10") or "10"))
    )
    tor_timeout = max(
        2, int(float(get_env(PUBLISHER_TOR_TIMEOUT_ENV, "5") or "5"))
    )

    report: Dict[str, Any] = {}

    # 1. Auto-setup — scans sources, downloads Tor if configured, probes
    #    Sci-Hub domains.  Fails fast (default 10 s) when optional deps
    #    (CloakBrowser, Tor) are not installed.
    try:
        result = await asyncio.wait_for(
            client.call_tool("scansci_pdf_auto_setup", {}),
            timeout=setup_timeout,
        )
        report["auto_setup"] = _parse_call_tool_result(result)
    except asyncio.TimeoutError:
        report["auto_setup"] = {
            "status": "timeout",
            "note": (
                f"Setup did not complete within {setup_timeout}s. "
                "Install scansci-pdf[cloakbrowser,tor] for publisher access."
            ),
        }
        logger.warning(
            "scansci_pdf_auto_setup timed out after %ss", setup_timeout
        )
    except Exception as exc:
        report["auto_setup"] = {"status": "error", "note": str(exc)[:200]}
        logger.warning("scansci_pdf_auto_setup failed: %s", exc)

    # 2. Tor start — fast timeout (default 5 s). Non-fatal; downloads
    #    gracefully degrade to OA-only sources when Tor is unavailable.
    try:
        result = await asyncio.wait_for(
            client.call_tool("scansci_pdf_tor_start", {}),
            timeout=tor_timeout,
        )
        report["tor_start"] = _parse_call_tool_result(result)
    except asyncio.TimeoutError:
        report["tor_start"] = {
            "status": "timeout",
            "note": f"Tor did not start within {tor_timeout}s.",
        }
        logger.warning("scansci_pdf_tor_start timed out after %ss", tor_timeout)
    except Exception as exc:
        report["tor_start"] = {"status": "error", "note": str(exc)[:200]}
        logger.warning("scansci_pdf_tor_start failed: %s", exc)

    _scansci_setup_done = True

    # ── Post-setup component status ────────────────────────────
    components = _detect_publisher_components()
    report["components"] = components
    available_components = [k for k, v in components.items() if v.get("available")]
    missing_components = [k for k, v in components.items() if not v.get("available")]

    if missing_components:
        logger.warning(
            "Publisher components missing: %s. "
            "Downloads will use OA-only sources. "
            "Install: pip install %s",
            ", ".join(missing_components),
            " ".join(
                _PUBLISHER_COMPONENT_PIP_TARGETS.get(m, m)
                for m in missing_components
            ),
        )
    if available_components:
        logger.info(
            "Publisher components ready: %s.", ", ".join(available_components)
        )

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_call_tool_result(result: Any) -> Dict[str, Any]:
    """Extract a JSON dict from a fastmcp / MCP SDK ``CallToolResult``."""
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(str(result))
    except (json.JSONDecodeError, Exception):
        return {}


def _download_arxiv_preprint(
    arxiv_id: str,
    save_dir: str,
    timeout: float = 30.0,
    max_attempts: int = 3,
) -> Optional[str]:
    """Download an arXiv preprint PDF, returning the output path or ``None``.

    Uses the well-known arXiv PDF URL pattern — no API key or platform
    instantiation required.
    """
    output = Path(save_dir) / f"{arxiv_id}.pdf"
    if output.exists() and output.stat().st_size > 1024:
        # Quick validity check: PDF magic bytes
        with open(output, "rb") as fh:
            if fh.read(4) == b"%PDF":
                logger.info("arXiv %s already cached at %s", arxiv_id, output)
                return str(output)

    os.makedirs(save_dir, exist_ok=True)
    candidates = [
        f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        f"https://arxiv.org/pdf/{arxiv_id}",
    ]

    last_error: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        for url in candidates:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"Accept": "application/pdf,*/*;q=0.8"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                if not data or len(data) < 1024:
                    continue
                if data[:4] != b"%PDF":
                    logger.warning(
                        "arXiv %s: response from %s is not a PDF (%d bytes)",
                        arxiv_id, url, len(data),
                    )
                    continue
                output.write_bytes(data)
                logger.info(
                    "arXiv %s downloaded → %s (%d bytes, attempt %d)",
                    arxiv_id, output, len(data), attempt,
                )
                return str(output)
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_attempts:
                    time.sleep(attempt * 1.5)

    logger.warning("arXiv %s download failed: %s", arxiv_id, last_error)
    return None


def _extract_paper_metadata(read_parsed, paper_key: str) -> Optional[Dict[str, Any]]:
    """Return parsed metadata dict for *paper_key*, or ``None`` if not found."""
    metadata = read_parsed(paper_key, "metadata")
    if not metadata or not isinstance(metadata, dict):
        return None
    return {
        "paper_key": paper_key,
        "source": str(metadata.get("source") or "").strip().lower(),
        "doi": str(metadata.get("doi") or "").strip(),
        "paper_id": str(metadata.get("paper_id") or "").strip(),
        "title": str(metadata.get("title") or "").strip(),
    }


def _resolve_identifier(meta: Dict[str, Any], _extract_arxiv_id) -> tuple:
    """Return ``(identifier, identifier_type)`` for scansci-pdf.

    identifier_type is ``"doi"``, ``"arxiv_id"``, or ``""`` (not usable).
    """
    doi = meta["doi"]
    arxiv_id = _extract_arxiv_id(meta["paper_id"], doi, meta["title"])

    # Fallback: _extract_arxiv_id's ARXIV_ID_RE matches the compact
    # "YYMMNNNNN" format without a dot, but standard arXiv IDs use the
    # dotted "YYMM.NNNNN" format (e.g. "1706.03762").  Handle both.
    if not arxiv_id:
        paper_id = str(meta.get("paper_id") or "")
        m = re.match(r"^(\d{4}\.\d{4,5})$", paper_id)
        if m:
            arxiv_id = m.group(1)

    if doi and not doi.startswith(_ARXIV_DOI_PREFIX):
        return doi, "doi"
    if arxiv_id:
        return arxiv_id, "arxiv_id"
    return "", ""


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_publisher_tools(mcp):  # noqa: C901
    """Register publisher-version download tools on *mcp*."""

    from ..cache import read_parsed, record_download, sha256_file
    from ..engine.download import _is_valid_pdf_file
    from ..engine.paper import _extract_arxiv_id
    from ..utils import DEFAULT_SAVE_PATH, resolve_save_path

    # ── shared core logic (single paper) ──────────────────────────────

    async def _download_one_publisher_version(
        paper_key: str,
        save_path: str,
        timeout: int,
        force_reparse: bool,
    ) -> Dict[str, Any]:
        """Core logic for downloading one publisher PDF.  Used by both the
        single and batch tools.

        When the paper is not yet in the parsed cache, the arXiv preprint
        is auto-downloaded first and minimal metadata is written to the
        cache so the publisher download can proceed — no separate MinerU
        parsing step required.
        """
        resolved_save = resolve_save_path(save_path)

        # 1. Read cached paper metadata — or auto-download arXiv when missing
        meta = await asyncio.to_thread(
            _extract_paper_metadata, read_parsed, paper_key
        )
        auto_downloaded = False

        if meta is None:
            # ── Auto-download arXiv preprint to seed the cache ──────
            arxiv_id = _extract_arxiv_id(paper_key, "", "")
            if not arxiv_id:
                # paper_key format: "arxiv_1706.03762" or "arxiv_1706.03762v7"
                m = re.match(
                    r"^arxiv_(\d{4}\.\d{4,5})(?:v\d+)?$",
                    paper_key,
                    re.IGNORECASE,
                )
                if m:
                    arxiv_id = m.group(1)
            if not arxiv_id:
                return {
                    "status": "not_found",
                    "paper_key": paper_key,
                    "message": (
                        f"No parsed paper found for key '{paper_key}' and "
                        "could not extract an arXiv ID from the key. "
                        "Download the arXiv PDF first, or use a key of the "
                        "form 'arxiv_XXXX_XXXXX'."
                    ),
                }

            logger.info(
                "Paper '%s' not in cache — auto-downloading arXiv %s …",
                paper_key, arxiv_id,
            )
            pdf_path = await asyncio.to_thread(
                _download_arxiv_preprint,
                arxiv_id,
                str(resolved_save),
            )
            if pdf_path is None:
                return {
                    "status": "download_failed",
                    "paper_key": paper_key,
                    "arxiv_id": arxiv_id,
                    "message": (
                        f"Failed to auto-download arXiv {arxiv_id}. "
                        "Check your network connection and try again."
                    ),
                }

            # Write minimal cache metadata (no MinerU parsing needed)
            await asyncio.to_thread(
                record_download,
                pdf_path=pdf_path,
                paper_key_hint=paper_key,
                source="arxiv",
                paper_id=arxiv_id,
                doi="",
                title=arxiv_id,
                downloader="auto-download-for-publisher",
                legal_status="preprint",
            )
            # Re-read metadata from cache
            meta = await asyncio.to_thread(
                _extract_paper_metadata, read_parsed, paper_key
            )
            if meta is None:
                return {
                    "status": "error",
                    "paper_key": paper_key,
                    "message": (
                        "arXiv PDF was downloaded and cached, but metadata "
                        "could not be read back. This is a cache consistency "
                        "error — try again."
                    ),
                }
            auto_downloaded = True
            logger.info(
                "arXiv %s auto-downloaded → cache key '%s' populated.",
                arxiv_id, paper_key,
            )

        # 2. Determine the best identifier for scansci-pdf
        identifier, identifier_type = _resolve_identifier(meta, _extract_arxiv_id)
        if not identifier:
            return {
                "status": "not_applicable",
                "paper_key": paper_key,
                "message": (
                    "Paper has no usable identifier for scansci-pdf. "
                    "This tool works for arXiv papers that have a "
                    "publisher DOI or arXiv ID. "
                    f"Detected source: '{meta['source']}'."
                ),
                "source": meta["source"],
            }

        # 3. Lazy-init scansci-pdf client
        client = _get_scansci_client()
        if client is None:
            return {
                "status": "unavailable",
                "paper_key": paper_key,
                "message": (
                    "scansci-pdf is not available. "
                    "Install manually: pip install scansci-pdf[cloakbrowser]  "
                    "or: uv pip install scansci-pdf[cloakbrowser]"
                ),
                "detail": _scansci_error,
            }

        # 4. One-time environment setup (Tor, browser, Sci-Hub domains)
        setup_report = await _ensure_scansci_ready(client)

        # 5. Remove stale .doi_index.json to prevent scansci-pdf from
        #    short-circuiting to a cached arXiv preprint instead of
        #    actually downloading the publisher version.
        save_dir = resolved_save
        doi_index = Path(save_dir) / ".doi_index.json"
        doi_index_existed = doi_index.exists()
        if doi_index_existed:
            try:
                doi_index.unlink()
                logger.info(
                    "Removed %s to allow fresh publisher download.", doi_index
                )
            except OSError as exc:
                logger.warning("Could not remove %s: %s", doi_index, exc)
        setup_report["doi_index_cleared"] = doi_index_existed

        # 6. Call scansci-pdf
        try:
            async with client:
                result = await asyncio.wait_for(
                    client.call_tool(
                        "scansci_pdf_smart_download",
                        {
                            "identifier": identifier,
                            "output_dir": str(save_dir),
                        },
                    ),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "paper_key": paper_key,
                "identifier_sent": identifier,
                "identifier_type": identifier_type,
                "timeout_seconds": timeout,
                "message": (
                    f"scansci-pdf did not complete within {timeout} s. "
                    "Retry with a higher timeout for large papers."
                ),
            }
        except Exception as exc:
            # Distinguish connection-lost errors (subprocess crash) from
            # other failures so the caller gets an actionable hint.
            exc_name = type(exc).__name__
            if exc_name in ("McpError", "BrokenResourceError", "ConnectionClosed"):
                logger.error(
                    "scansci-pdf connection lost for %s: %s", paper_key, exc
                )
                # Reset the dead client so subsequent calls create a fresh one
                global _scansci_client
                _scansci_client = None
                global _scansci_setup_done
                _scansci_setup_done = False
                # Check if missing Playwright browser may be the culprit
                pw = _check_playwright_browser()
                if (
                    _component_status.get("cloakbrowser")
                    and not pw.get("chromium_browser")
                ):
                    hint = (
                        " CloakBrowser is installed but the Playwright "
                        "Chromium browser may be missing (~182 MB). "
                        "Install: playwright install chromium"
                    )
                else:
                    hint = (
                        " Retry with a VPN or proxy configured "
                        "in scansci-pdf (scansci_pdf_config_set)."
                    )
                return {
                    "status": "error",
                    "paper_key": paper_key,
                    "identifier_sent": identifier,
                    "message": (
                        f"scansci-pdf process exited unexpectedly ({exc_name}). "
                        "This may happen when all download sources are blocked "
                        "by the network." + hint
                    ),
                    "error_type": exc_name,
                }
            logger.exception("scansci-pdf call_tool failed for %s", paper_key)
            return {
                "status": "error",
                "paper_key": paper_key,
                "identifier_sent": identifier,
                "message": f"scansci-pdf call failed: {exc}",
            }

        # 7. Parse scansci-pdf response
        scansci_data = _parse_call_tool_result(result)
        if not scansci_data.get("success"):
            return {
                "status": "download_failed",
                "paper_key": paper_key,
                "identifier_sent": identifier,
                "identifier_type": identifier_type,
                "message": (
                    "scansci-pdf was unable to download the publisher "
                    "version for this paper."
                ),
                "scansci_result": scansci_data,
            }

        pdf_path = scansci_data.get("file", "")
        download_source = scansci_data.get("source", "unknown")

        # 7. Validate the downloaded PDF
        if not pdf_path or not _is_valid_pdf_file(pdf_path):
            return {
                "status": "invalid_pdf",
                "paper_key": paper_key,
                "identifier_sent": identifier,
                "scansci_file": pdf_path,
                "message": (
                    "scansci-pdf returned a path that does not point "
                    "to a valid PDF file."
                ),
            }

        pdf_sha256 = await asyncio.to_thread(sha256_file, pdf_path)
        try:
            pdf_bytes = os.path.getsize(os.path.expanduser(pdf_path))
        except OSError:
            pdf_bytes = 0

        # 8. Record download in paper-search cache
        await asyncio.to_thread(
            record_download,
            pdf_path=pdf_path,
            paper_key_hint=paper_key,
            source=meta["source"],
            paper_id=meta["paper_id"],
            doi=meta["doi"],
            title=meta["title"],
            downloader="scansci-pdf",
            legal_status="publisher",
        )

        response: Dict[str, Any] = {
            "status": "ok",
            "paper_key": paper_key,
            "identifier_sent": identifier,
            "identifier_type": identifier_type,
            "publisher_pdf": pdf_path,
            "download_source": download_source,
            "downloader": "scansci-pdf",
            "pdf_sha256": pdf_sha256,
            "pdf_bytes": pdf_bytes,
            "was_parsed": False,
            "auto_downloaded_arxiv": auto_downloaded,
            "setup": setup_report,
        }
        arxiv_id = _extract_arxiv_id(
            meta["paper_id"], meta["doi"], meta["title"]
        )
        if arxiv_id:
            response["arxiv_id"] = arxiv_id
        if identifier_type == "doi":
            response["publisher_doi"] = meta["doi"]

        # 9. Optional re-parse with MinerU
        if force_reparse:
            try:
                from ..parsers.mineru import (  # noqa: PLC0415
                    parse_pdf_with_mineru as run_parse,
                )

                parse_result = await asyncio.to_thread(
                    run_parse,
                    pdf_path,
                    paper_key_hint=paper_key,
                    source=meta["source"],
                    paper_id=meta["paper_id"],
                    doi=meta["doi"],
                    title=meta["title"],
                    mode="auto",
                    force=True,
                )
                response["was_parsed"] = True
                response["parse_result"] = parse_result
            except Exception as exc:
                logger.exception(
                    "MinerU re-parse after publisher download failed"
                )
                response["was_parsed"] = False
                response["parse_error"] = str(exc)

        return response

    # ══════════════════════════════════════════════════════════════════
    # Tool: download_publisher_version  (single paper)
    # ══════════════════════════════════════════════════════════════════

    @mcp.tool()
    async def download_publisher_version(
        paper_key: str,
        save_path: str = DEFAULT_SAVE_PATH,
        timeout: int = 120,
        force_reparse: bool = False,
    ) -> Dict[str, Any]:
        """Download the publisher final version of a cached arXiv paper.

        Connects to the external **scansci-pdf** MCP server (auto-installed
        on first use) to locate and download the publisher's version of
        record.  scansci-pdf races 13+ download sources — including
        anti-detection browser engine (CloakBrowser), Tor proxy, Sci-Hub,
        LibGen, Unpaywall, OpenAlex, and publisher-direct routes — to
        maximise the chance of retrieving the final published PDF.

        **Prerequisites**

        * Internet access (scansci-pdf is auto-installed via pip on first
          call if not already present).
        * The paper must already be parsed in the paper-search-mcp cache
          (use ``list_parsed_papers`` to see what is available).
        * The paper must have an arXiv ID or a publisher DOI.

        Args:
            paper_key: Parsed-paper key from the paper-search-mcp cache
                (e.g. ``"arxiv_1706_03762"``).
            save_path: Directory where the publisher PDF is saved.
            timeout: Maximum seconds per paper (default 120).
            force_reparse: If ``True``, also parse the publisher PDF with
                MinerU after download.

        Returns:
            A dict with ``status`` (one of ``"ok"``, ``"not_found"``,
            ``"not_applicable"``, ``"unavailable"``, ``"download_failed"``,
            ``"timeout"``, ``"invalid_pdf"``, ``"error"``) and, on success,
            ``publisher_pdf``, ``download_source``, ``pdf_sha256``, etc.
        """
        return await _download_one_publisher_version(
            paper_key, save_path, timeout, force_reparse
        )

    # ══════════════════════════════════════════════════════════════════
    # Tool: batch_download_publisher_versions  (multiple papers)
    # ══════════════════════════════════════════════════════════════════

    @mcp.tool()
    async def batch_download_publisher_versions(
        paper_keys: str,
        save_path: str = DEFAULT_SAVE_PATH,
        timeout: int = 300,
        force_reparse: bool = False,
    ) -> Dict[str, Any]:
        """Download publisher versions for multiple cached arXiv papers.

        Accepts a comma-separated list of paper keys (or ``"all"`` to
        process every arXiv paper in the parsed cache) and downloads the
        publisher final version for each in sequence.  The scansci-pdf
        subprocess is kept alive between papers for speed.

        Args:
            paper_keys: Comma-separated paper keys (e.g.
                ``"arxiv_1706_03762,arxiv_1810_04805"``) or ``"all"`` to
                process every cached paper whose source is ``"arxiv"``.
            save_path: Directory where publisher PDFs are saved.
            timeout: Maximum seconds for the **entire batch** (default 300).
            force_reparse: If ``True``, also parse each publisher PDF with
                MinerU after download.

        Returns:
            A dict with ``total``, ``ok``, ``failed`` counts and a
            ``results`` list of per-paper status dicts.
        """
        # Resolve paper_keys
        raw = [k.strip() for k in (paper_keys or "").split(",") if k.strip()]
        if not raw:
            return {
                "status": "error",
                "message": "paper_keys is required (comma-separated list or 'all').",
            }

        if len(raw) == 1 and raw[0].lower() == "all":
            # Collect every arXiv paper from the cache
            all_entries = await asyncio.to_thread(read_parsed, "__list__", "metadata")
            # read_parsed doesn't support __list__ — use the list function instead
            from ..cache import list_parsed as _list_parsed

            entries = await asyncio.to_thread(_list_parsed)
            raw = [
                e["paper_key"]
                for e in entries
                if (e.get("source") or "").strip().lower() == "arxiv"
            ]
            if not raw:
                return {
                    "status": "not_found",
                    "message": "No arXiv papers found in the parsed cache.",
                }

        total = len(raw)
        results: List[Dict[str, Any]] = []
        ok_count = 0
        failed_count = 0

        for paper_key in raw:
            try:
                r = await asyncio.wait_for(
                    _download_one_publisher_version(
                        paper_key, save_path, timeout, force_reparse
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                r = {
                    "status": "timeout",
                    "paper_key": paper_key,
                    "message": (
                        f"Batch timeout ({timeout}s) reached during {paper_key}."
                    ),
                }

            results.append(r)
            if r.get("status") == "ok":
                ok_count += 1
            else:
                failed_count += 1

        return {
            "status": "ok",
            "total": total,
            "ok": ok_count,
            "failed": failed_count,
            "results": results,
        }

    # ══════════════════════════════════════════════════════════════════
    # Tool: check_publisher_setup  (diagnostics)
    # ══════════════════════════════════════════════════════════════════

    @mcp.tool()
    async def check_publisher_setup() -> Dict[str, Any]:
        """Check scansci-pdf environment: installation, Tor, browser, sources.

        Returns the current state of the scansci-pdf toolchain so you can
        see what is configured and what may need attention before running
        ``download_publisher_version``.
        """
        report: Dict[str, Any] = {
            "scansci_pdf_installed": False,
            "client_available": False,
        }

        # 1. Is the Python package importable?
        try:
            import scansci_pdf  # noqa: F401, PLC0415
            report["scansci_pdf_installed"] = True
        except ImportError:
            report["scansci_pdf_installed"] = False
            report["message"] = (
                "scansci-pdf is not installed. It will be auto-installed "
                "on first use of download_publisher_version. "
                "To pre-install: uv sync --extra publisher  "
                "or: uv pip install scansci-pdf[cloakbrowser]"
            )
            return report

        # 2. Component detection (CloakBrowser, Tor, Crypto)
        components = _detect_publisher_components()
        report["components"] = {
            name: {
                "available": info["available"],
                "description": info["description"],
                "pip_target": _PUBLISHER_COMPONENT_PIP_TARGETS.get(
                    name, name
                ),
            }
            for name, info in components.items()
        }
        missing = [k for k, v in components.items() if not v.get("available")]
        if missing:
            report["components_missing"] = missing
            report["install_hint"] = (
                "pip install "
                + " ".join(
                    _PUBLISHER_COMPONENT_PIP_TARGETS.get(m, m) for m in missing
                )
                + "  |  uv pip install "
                + " ".join(
                    _PUBLISHER_COMPONENT_PIP_TARGETS.get(m, m) for m in missing
                )
            )
            report["publisher_access"] = (
                "limited"
                if "cloakbrowser" in missing
                else "partial"
            )

        # ── Playwright browser status (needed by CloakBrowser) ────
        pw_status = _check_playwright_browser()
        report["playwright"] = pw_status
        if (
            report.get("components", {}).get("cloakbrowser", {}).get("available")
            and not pw_status.get("chromium_browser")
        ):
            report["playwright_missing_hint"] = (
                "CloakBrowser is installed but the Playwright Chromium "
                "browser is missing (~182 MB). Install it with: "
                "playwright install chromium"
            )

        # 3. Can we connect to the scansci-pdf MCP server?
        client = _get_scansci_client()
        if client is None:
            report["client_available"] = False
            report["client_error"] = _scansci_error
            return report
        report["client_available"] = True

        # 4. Run health check inside scansci-pdf
        try:
            async with client:
                result = await client.call_tool(
                    "scansci_pdf_health_check", {"detailed": True}
                )
                report["health"] = _parse_call_tool_result(result)
        except Exception as exc:
            report["health"] = {"error": str(exc)}

        return report
