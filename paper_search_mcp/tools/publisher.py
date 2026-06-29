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
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..cache import read_parsed, record_download, sha256_file
from ..config import get_env
from ..engine.download import _is_valid_pdf_file
from ..engine.paper import _extract_arxiv_id
from ..utils import DEFAULT_SAVE_PATH, resolve_save_path

# Exception types for isinstance-based connection-lost detection (P2-2).
# Wrapped in try/except so the module still loads when fastmcp or anyio
# are temporarily absent.
try:
    from fastmcp.exceptions import McpError  # noqa: F401
except ImportError:  # pragma: no cover
    McpError = None  # type: ignore[assignment]

try:
    from anyio import BrokenResourceError, ClosedResourceError  # noqa: F401
except ImportError:  # pragma: no cover
    BrokenResourceError = None  # type: ignore[assignment]
    ClosedResourceError = None  # type: ignore[assignment]

# Tuple of exception *classes* that indicate a lost scansci-pdf subprocess
# connection (transport-level failure).  None entries are filtered out.
_CONNECTION_LOST_EXCEPTIONS: tuple = tuple(
    filter(None, (McpError, BrokenResourceError, ClosedResourceError))
)

# Fallback: exception *names* used when the class-based tuple is empty
# (e.g. fastmcp/anyio not installed).  Mirrors the original string-matching
# approach for graceful degradation.
_CONNECTION_LOST_NAMES: frozenset = frozenset(
    {"McpError", "BrokenResourceError", "ClosedResourceError"}
)

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

# Track whether API keys have been injected into scansci-pdf.  Keys
# persist to scansci-pdf's config file, so we only inject once per
# client lifecycle (until the client is reset).
_keys_injected: bool = False

# Track whether Tor is available so we can adapt setup timeouts.
_tor_available: Optional[bool] = None  # tri-state: None=unchecked

# Lock protecting client creation, setup, and crash-recovery resets.
# Prevents concurrent _download_one_publisher_version calls from racing
# on the shared _scansci_client / _scansci_setup_done state (P3-1).
_scansci_lock: asyncio.Lock = asyncio.Lock()

# Module-level reference to the core download function.
# Set by register_publisher_tools; importers MUST guard with None check.
# Used by server.py for IEEE/ACM download routing to scansci-pdf.
download_one_publisher_version = None


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

    Uses a fast filesystem path check (< 0.01 s) before falling back to the
    slow ``playwright install --dry-run`` subprocess (up to 30 s on Windows).
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

    # 2. Fast path: check filesystem for Chromium browser binaries.
    #    This avoids the 30 s subprocess timeout on Windows and is
    #    nearly instant on all platforms.
    _chromium_dirs = _find_playwright_chromium_dirs()
    if _chromium_dirs:
        return {
            "playwright_available": True,
            "chromium_browser": True,
            "message": (
                "Playwright Chromium browser ready "
                f"(found at {_chromium_dirs[0]})"
            ),
        }

    # 3. Slow fallback: use playwright CLI to check.
    #    Only reached when no filesystem match was found — this may
    #    indicate a non-standard installation path.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
            timeout=15,
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
            "message": "Playwright Chromium browser ready (verified via CLI)",
        }
    except Exception as exc:
        return {
            "playwright_available": True,
            "chromium_browser": None,
            "message": f"Could not determine browser status: {exc}",
        }


def _find_playwright_chromium_dirs() -> List[str]:
    """Find Playwright Chromium browser installation paths on disk.

    Checks platform-specific cache directories:
    * Windows: ``%LOCALAPPDATA%\\ms-playwright\\chromium-*\\chrome-win\\chrome.exe``
    * macOS: ``~/Library/Caches/ms-playwright/chromium-*``
    * Linux: ``~/.cache/ms-playwright/chromium-*``

    Returns a list of matching chromium binary paths (sorted).
    """
    candidates: List[Path] = []

    # Platform-specific cache roots
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            candidates.append(Path(local_appdata) / "ms-playwright")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    else:
        candidates.append(Path.home() / ".cache" / "ms-playwright")

    found: List[str] = []
    for cache_dir in candidates:
        if not cache_dir.exists():
            continue
        # Chromium directories follow the pattern chromium-{version}
        for chromium_dir in sorted(cache_dir.glob("chromium-*"), reverse=True):
            if not chromium_dir.is_dir():
                continue
            # Platform-specific binary paths
            if sys.platform == "win32":
                chrome_exe = chromium_dir / "chrome-win" / "chrome.exe"
            elif sys.platform == "darwin":
                # macOS: Chromium.app/Contents/MacOS/Chromium
                chrome_exe = (
                    chromium_dir / "chrome-mac" / "Chromium.app"
                    / "Contents" / "MacOS" / "Chromium"
                )
            else:
                chrome_exe = chromium_dir / "chrome-linux" / "chrome"
            if chrome_exe.exists():
                found.append(str(chrome_exe))
    return found


async def _install_playwright_chromium_async(timeout: int = 300) -> bool:
    """Async, non-blocking install of the Playwright Chromium browser (~182 MB).

    Uses ``asyncio.create_subprocess_exec`` so the MCP event loop stays
    alive during the download — prevents ``-32000 Connection closed``
    timeouts that occur with the synchronous ``subprocess.run`` approach.
    """
    logger.info(
        "  [async] playwright install chromium (timeout=%ds) …", timeout
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m", "playwright", "install", "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        if proc.returncode == 0:
            logger.info("  ✓ Playwright Chromium installed.")
            return True
        stderr_text = (stderr or b"").decode("utf-8", errors="replace")
        logger.warning(
            "  ✗ playwright install chromium failed (rc=%d): %s",
            proc.returncode, stderr_text[-300:],
        )
        return False
    except asyncio.TimeoutError:
        logger.warning(
            "  ✗ playwright install chromium timed out after %ds.",
            timeout,
        )
        try:
            proc.kill()
        except Exception:
            pass
        return False
    except Exception as exc:
        logger.warning(
            "  ✗ playwright install chromium error: %s", exc
        )
        return False


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

    .. note::
        This is the legacy synchronous version kept for backwards
        compatibility (e.g. called from non-async contexts).  New code
        should prefer :func:`_run_pip_install_async` to avoid blocking
        the asyncio event loop.
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


async def _run_pip_install_async(
    package_spec: str, timeout: int = 300
) -> bool:
    """Async version of :func:`_run_pip_install` that does **not** block the
    asyncio event loop.

    Uses ``asyncio.create_subprocess_exec`` so the MCP server stays
    responsive during long-running pip installs (avoids the
    ``-32000 Connection closed`` timeout).
    """
    async def _install_one(cmd: List[str], label: str) -> bool:
        logger.info("  [async] %s: %s", label, " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            if proc.returncode == 0:
                return True
            stderr_text = (stderr or b"").decode("utf-8", errors="replace")
            logger.warning(
                "  [async] %s failed (rc=%d): %s",
                label, proc.returncode, stderr_text[-300:],
            )
            return False
        except asyncio.TimeoutError:
            logger.warning(
                "  [async] %s timed out after %ds.", label, timeout
            )
            try:
                proc.kill()
            except Exception:
                pass
            return False
        except Exception as exc:
            logger.warning("  [async] %s error: %s", label, exc)
            return False

    # Attempt 1: standard pip
    if await _install_one(
        [sys.executable, "-m", "pip", "install", package_spec],
        f"pip install {package_spec}",
    ):
        return True

    # Attempt 2: uv pip install (fallback when venv lacks pip)
    return await _install_one(
        ["uv", "pip", "install", package_spec],
        f"uv pip install {package_spec} (fallback)",
    )


def _install_missing_components(
    missing: List[str],
    install_timeout: int = 120,
) -> Dict[str, bool]:
    """Attempt to pip-install a list of missing Python packages.

    Returns a dict mapping each package name → whether it is now importable.

    .. note::
        Legacy synchronous version.  New code should prefer
        :func:`_install_missing_components_async`.
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


async def _install_missing_components_async(
    missing: List[str],
    install_timeout: int = 120,
) -> Dict[str, bool]:
    """Async version of :func:`_install_missing_components`.

    Uses non-blocking ``asyncio.create_subprocess_exec`` under the hood
    so the MCP event loop stays alive during installation.
    """
    results: Dict[str, bool] = {}
    for mod_name in missing:
        pip_target = _PUBLISHER_COMPONENT_PIP_TARGETS.get(mod_name, mod_name)
        logger.info(
            "  [async] Attempting to install %s (pip: %s) …",
            mod_name, pip_target,
        )
        if await _run_pip_install_async(pip_target, timeout=install_timeout):
            # Verify the module is now importable
            import importlib as _importlib
            try:
                _importlib.import_module(mod_name)
                results[mod_name] = True
                logger.info("  ✓ %s installed and importable.", mod_name)
            except ImportError:
                results[mod_name] = False
                logger.warning(
                    "  ✗ %s installed but still not importable.", mod_name
                )
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


async def _auto_install_scansci_pdf_async() -> bool:
    """Async version of :func:`_auto_install_scansci_pdf`.

    Uses non-blocking ``asyncio.create_subprocess_exec`` throughout so
    the MCP event loop stays responsive.  This prevents the ``-32000
    Connection closed`` timeout that can occur when the synchronous
    version blocks for 60–300 s during pip install.

    Phases:
    1. Install scansci-pdf base (async, non-blocking).
    2. Detect which optional components are still missing.
    3. Async-install each missing component.
    4. Report final status.

    Returns ``True`` if scansci-pdf itself is importable afterwards.
    """
    global _scansci_install_attempted, _scansci_importable, _component_status

    # Fast path: already importable (cached)
    if _scansci_importable is True:
        return True

    # Fast path: check if importable now
    try:
        import scansci_pdf  # noqa: F401
        _scansci_importable = True
        _detect_publisher_components()
        return True
    except ImportError:
        pass

    if _scansci_install_attempted:
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
    # Phase 1: Async-install scansci-pdf base
    # ══════════════════════════════════════════════════════════════════
    logger.info(
        "scansci-pdf not found — attempting async auto-install …"
    )
    install_ok = False
    for attempt, install_target in enumerate([
        "scansci-pdf[cloakbrowser]",
        "scansci-pdf",
    ]):
        logger.info(
            "  [async] install %s (attempt %d) …",
            install_target, attempt + 1,
        )
        if await _run_pip_install_async(install_target, timeout=300):
            install_ok = True
            break
        else:
            logger.warning(
                "  [async] %s install failed, trying fallback.",
                install_target,
            )

    if not install_ok:
        logger.warning(
            "scansci-pdf async auto-install failed completely."
        )
        _scansci_importable = False
        return False

    # Verify scansci-pdf is now importable
    try:
        import scansci_pdf  # noqa: F401, PLC0415
        _scansci_importable = True
        logger.info("scansci-pdf base package installed ✓")
    except ImportError:
        logger.warning(
            "scansci-pdf installed but still not importable"
        )
        _scansci_importable = False
        return False

    # ══════════════════════════════════════════════════════════════════
    # Phase 2: Detect and async-install missing optional components
    # ══════════════════════════════════════════════════════════════════
    components = _detect_publisher_components()
    missing = [
        name for name, info in components.items()
        if not info.get("available")
    ]

    if missing:
        logger.info(
            "Missing publisher-access components: %s "
            "— attempting async auto-install …",
            ", ".join(missing),
        )
        install_results = await _install_missing_components_async(missing)

        still_missing = [k for k, v in install_results.items() if not v]
        if still_missing:
            logger.warning(
                "Could not async auto-install: %s. "
                "Publisher downloads will use OA-only sources. "
                "Install manually: pip install %s",
                ", ".join(still_missing),
                " ".join(
                    _PUBLISHER_COMPONENT_PIP_TARGETS.get(m, m)
                    for m in still_missing
                ),
            )
        else:
            logger.info(
                "All publisher-access components installed ✓"
            )
    else:
        logger.info(
            "All publisher-access components already available ✓"
        )

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


async def _get_scansci_client() -> Optional[Any]:
    """Return a connected scansci-pdf MCP client, or ``None`` if unavailable.

    * Auto-installs scansci-pdf on first call (if not already present).
    * Retries connection on each call when scansci-pdf is importable but
      the previous connection attempt failed — no permanent failure cache.
    * Uses ``_scansci_lock`` to prevent concurrent client creation (P3-1).
    """
    global _scansci_client, _scansci_error, _scansci_importable

    # Fast path: already connected (lock-free read is safe — only
    # transitions from str/None → Client, never back to str/None
    # without holding the lock).
    if _scansci_client is not None:
        return _scansci_client

    # Check importability (cached to avoid repeated import attempts)
    if _scansci_importable is None:
        try:
            import scansci_pdf  # noqa: F401, PLC0415
            _scansci_importable = True
        except ImportError:
            _scansci_importable = False

    # ── Ensure scansci-pdf is installed (async, non-blocking) ─────
    if not _scansci_importable and not await _auto_install_scansci_pdf_async():
        _scansci_error = (
            "scansci-pdf could not be installed. "
            "Install manually: pip install scansci-pdf[cloakbrowser]  "
            "or: uv pip install scansci-pdf[cloakbrowser]"
        )
        return None

    # ── Create the MCP client via stdio transport ───────────────
    # Hold the lock around client creation to prevent concurrent
    # callers from spawning multiple scansci-pdf subprocesses (P3-1).
    async with _scansci_lock:
        # Double-check: another caller may have created the client
        # while we were waiting for the lock.
        if _scansci_client is not None:
            return _scansci_client

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
                "scansci-pdf MCP client initialised (timeout=%ss).",
                client_timeout,
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

    Uses ``_scansci_lock`` to prevent concurrent setup runs (P3-1).
    """
    global _scansci_setup_done

    # Fast path: already done (lock-free read is safe).
    if _scansci_setup_done:
        return {"setup": "already_done"}

    # Acquire lock to prevent concurrent setup on the same client (P3-1).
    async with _scansci_lock:
        # Double-check after acquiring the lock.
        if _scansci_setup_done:
            return {"setup": "already_done"}

        # ── Adaptive setup timeout.  The first setup may need to
        #    download the Tor Expert Bundle (~22 MB) which takes
        #    ~20-30 s.  Once Tor is cached, 10 s is sufficient.
        global _tor_available, _keys_injected

        _base_setup = int(float(
            get_env(PUBLISHER_SETUP_TIMEOUT_ENV, "10") or "10"
        ))
        if _tor_available is None:
            # First setup — allow extra time for Tor download
            setup_timeout = max(_base_setup, 60)
        elif _tor_available:
            setup_timeout = max(_base_setup, 5)
        else:
            setup_timeout = _base_setup
        tor_timeout = max(
            2, int(float(get_env(PUBLISHER_TOR_TIMEOUT_ENV, "5") or "5"))
        )

        report: Dict[str, Any] = {}

        # All scansci-pdf tool calls must be inside an async with client:
        # context (fastmcp requirement).  We open one context for the
        # entire setup sequence to avoid repeated enter/exit overhead.
        try:
            async with client:
                # 1. Auto-setup — scans sources, downloads Tor if configured,
                #    probes Sci-Hub domains.
                try:
                    result = await asyncio.wait_for(
                        client.call_tool("scansci_pdf_auto_setup", {}),
                        timeout=setup_timeout,
                    )
                    parsed = _parse_call_tool_result(result)
                    report["auto_setup"] = parsed
                    # Track Tor availability from setup result
                    tor_status = (parsed.get("status") or {}).get("tor", "")
                    _tor_available = tor_status == "running"
                except asyncio.TimeoutError:
                    # ── First auto_setup timed out.  The Tor Expert Bundle
                    #    (~22 MB) may still be downloading.  Retry once with
                    #    a longer timeout before giving up.
                    _retry_timeout = max(setup_timeout * 2, 120)
                    if _tor_available is None and setup_timeout < _retry_timeout:
                        logger.warning(
                            "scansci_pdf_auto_setup timed out after %ss "
                            "— retrying with %ss for Tor download …",
                            setup_timeout, _retry_timeout,
                        )
                        try:
                            result = await asyncio.wait_for(
                                client.call_tool("scansci_pdf_auto_setup", {}),
                                timeout=_retry_timeout,
                            )
                            parsed = _parse_call_tool_result(result)
                            report["auto_setup"] = parsed
                            report["auto_setup"]["retried_after_timeout"] = True
                            tor_status = (parsed.get("status") or {}).get("tor", "")
                            _tor_available = tor_status == "running"
                        except asyncio.TimeoutError:
                            report["auto_setup"] = {
                                "status": "timeout",
                                "note": (
                                    f"Setup did not complete within "
                                    f"{_retry_timeout}s after retry. "
                                    "Install scansci-pdf[cloakbrowser,tor] "
                                    "for publisher access."
                                ),
                            }
                            logger.warning(
                                "scansci_pdf_auto_setup retry also timed out "
                                "after %ss", _retry_timeout,
                            )
                    else:
                        report["auto_setup"] = {
                            "status": "timeout",
                            "note": (
                                f"Setup did not complete within "
                                f"{setup_timeout}s. "
                                "Install scansci-pdf[cloakbrowser,tor] for "
                                "publisher access."
                            ),
                        }
                        logger.warning(
                            "scansci_pdf_auto_setup timed out after %ss",
                            setup_timeout,
                        )

                # 2. Tor start — fast timeout (default 5 s).  Non-fatal.
                try:
                    result = await asyncio.wait_for(
                        client.call_tool("scansci_pdf_tor_start", {}),
                        timeout=tor_timeout,
                    )
                    tor_parsed = _parse_call_tool_result(result)
                    report["tor_start"] = tor_parsed
                    if tor_parsed.get("running"):
                        _tor_available = True
                except asyncio.TimeoutError:
                    report["tor_start"] = {
                        "status": "timeout",
                        "note": f"Tor did not start within {tor_timeout}s.",
                    }
                    logger.warning(
                        "scansci_pdf_tor_start timed out after %ss",
                        tor_timeout,
                    )

                # 3. Inject configured API keys into scansci-pdf so its
                #    Phase-1/2 download sources (CORE, Unpaywall, Elsevier)
                #    benefit from higher rate limits.  Keys persist to
                #    scansci-pdf's config file, so we only inject once.
                if not _keys_injected:
                    _inject_keys: Dict[str, str] = {}
                    _unpaywall_email = get_env(
                        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL"
                    ) or get_env("UNPAYWALL_EMAIL")
                    if _unpaywall_email:
                        _inject_keys["email"] = _unpaywall_email
                    _core_key = get_env(
                        "PAPER_SEARCH_MCP_CORE_API_KEY"
                    ) or get_env("CORE_API_KEY")
                    if _core_key:
                        _inject_keys["core_api_key"] = _core_key
                    _elsevier_key = get_env(
                        "PAPER_SEARCH_MCP_ELSEVIER_API_KEY"
                    ) or get_env("ELSEVIER_API_KEY")
                    if _elsevier_key:
                        _inject_keys["elsevier_api_key"] = _elsevier_key
                    _openalex_key = get_env(
                        "PAPER_SEARCH_MCP_OPENALEX_API_KEY"
                    ) or get_env("OPENALEX_API_KEY")
                    if _openalex_key:
                        _inject_keys["openalex_api_key"] = _openalex_key

                    _injected = []
                    for _key, _val in _inject_keys.items():
                        try:
                            await asyncio.wait_for(
                                client.call_tool(
                                    "scansci_pdf_config_set",
                                    {"key": _key, "value": _val},
                                ),
                                timeout=5,
                            )
                            _injected.append(_key)
                        except Exception:
                            logger.debug(
                                "scansci_pdf_config_set(%s) skipped",
                                _key,
                                exc_info=True,
                            )
                    if _injected:
                        report["keys_injected"] = _injected
                        logger.info(
                            "Injected API keys into scansci-pdf: %s",
                            ", ".join(_injected),
                        )
                    _keys_injected = True

        except Exception as exc:
            # The async with or an inner call failed — log but don't block
            # the download (scansci-pdf still works with its defaults).
            logger.warning("scansci-pdf setup sequence failed: %s", exc)
            report["setup_error"] = str(exc)[:200]

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


def _cleanup_doi_index(save_dir: str) -> bool:
    """Remove the .doi_index.json cache file that scansci-pdf creates.

    scansci-pdf writes this file on every run.  If left in place it can
    cause subsequent downloads to short-circuit to a stale cached arXiv
    preprint instead of searching for the real publisher version.

    Returns ``True`` if the file was removed, ``False`` if it didn't exist.
    """
    doi_index = Path(save_dir) / ".doi_index.json"
    if not doi_index.exists():
        return False
    try:
        doi_index.unlink()
        logger.info("Cleaned up %s after download.", doi_index)
        return True
    except OSError as exc:
        logger.warning("Could not remove %s: %s", doi_index, exc)
        return False


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


def _lookup_real_publisher_doi(
    arxiv_id: str,
    max_retries: int = 3,
) -> Optional[str]:
    """Look up the real publisher DOI for an arXiv paper via Semantic Scholar.

    Many arXiv papers also have a published version (e.g. at a conference
    or journal).  Semantic Scholar's API maps arXiv IDs to the
    corresponding publisher DOI when one exists, which scansci-pdf can
    then use to retrieve the final published PDF.

    Retries on transient failures (429 rate-limit, 5xx server errors) with
    exponential backoff.

    Returns the real DOI string if found, or ``None`` if the lookup fails
    or no real (non-synthetic) DOI is available.
    """
    import json as _json

    # Normalise: strip version suffix and "arXiv:" prefix if present
    aid = arxiv_id.strip()
    if aid.lower().startswith("arxiv:"):
        aid = aid[6:]
    # SemSch requires the bare ID without version suffix for lookup
    aid = aid.split("v", 1)[0]

    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/"
        f"ArXiv:{aid}?fields=externalIds"
    )

    # Use the configured Semantic Scholar API key for higher rate limits
    # (100 req/s with key vs 1 req/s without).  Without a key the
    # unauthenticated tier is easily exhausted, causing persistent 429s.
    semsch_key = get_env("PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY")
    headers: Dict[str, str] = {"Accept": "application/json"}
    if semsch_key:
        headers["x-api-key"] = semsch_key

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.getcode()
                if status == 429:
                    # Rate-limited — retry with backoff
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        "Semantic Scholar rate-limited (429) for arXiv %s "
                        "(attempt %d/%d), retrying in %ds …",
                        arxiv_id, attempt, max_retries, backoff,
                    )
                    if attempt < max_retries:
                        time.sleep(backoff)
                        continue
                    return None
                if status >= 500:
                    # Server error — retry with backoff
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        "Semantic Scholar server error (%d) for arXiv %s "
                        "(attempt %d/%d), retrying in %ds …",
                        status, arxiv_id, attempt, max_retries, backoff,
                    )
                    if attempt < max_retries:
                        time.sleep(backoff)
                        continue
                    return None
                data = _json.loads(resp.read().decode("utf-8"))
                break  # Success — exit retry loop

        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                backoff = 2 ** (attempt - 1)
                logger.warning(
                    "Semantic Scholar rate-limited (429) for arXiv %s "
                    "(attempt %d/%d), retrying in %ds …",
                    arxiv_id, attempt, max_retries, backoff,
                )
                if attempt < max_retries:
                    time.sleep(backoff)
                    continue
            logger.warning(
                "Semantic Scholar DOI lookup HTTP %d for arXiv %s",
                exc.code, arxiv_id,
            )
            return None

        except Exception:
            if attempt < max_retries:
                backoff = 2 ** (attempt - 1)
                logger.debug(
                    "Semantic Scholar DOI lookup failed for arXiv %s "
                    "(attempt %d/%d), retrying in %ds …",
                    arxiv_id, attempt, max_retries, backoff,
                    exc_info=True,
                )
                time.sleep(backoff)
                continue
            logger.warning(
                "Semantic Scholar DOI lookup failed for arXiv %s "
                "after %d attempts",
                arxiv_id, max_retries,
                exc_info=True,
            )
            return None

    doi = (data.get("externalIds") or {}).get("DOI") or ""
    if doi and not doi.startswith(_ARXIV_DOI_PREFIX):
        logger.info(
            "Found real publisher DOI %s for arXiv %s via Semantic Scholar",
            doi, arxiv_id,
        )
        return doi

    logger.info(
        "No real publisher DOI found for arXiv %s "
        "(Semantic Scholar returned no external DOI or only synthetic DOI).",
        arxiv_id,
    )
    return None


def _lookup_doi_by_title_semsch(
    title: str,
    arxiv_id: str,
    max_retries: int = 2,
) -> Optional[str]:
    """Fallback: search Semantic Scholar by paper title to find a publisher DOI.

    Used when the direct arXiv-ID→DOI lookup returns no result.  The API key
    (if configured) is included for higher rate limits.

    Returns the publisher DOI string, or ``None`` if no match is found.
    """
    import json as _json
    from urllib.parse import quote

    semsch_key = get_env("PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY")
    headers: Dict[str, str] = {"Accept": "application/json"}
    if semsch_key:
        headers["x-api-key"] = semsch_key

    # Use the first 200 chars of the title as the search query
    query = quote(title[:200])
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={query}&limit=10&fields=title,externalIds,year"
    )

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                backoff = 2 ** (attempt - 1)
                logger.debug(
                    "Semantic Scholar title search rate-limited for %s "
                    "(attempt %d/%d), retrying in %ds …",
                    arxiv_id, attempt, max_retries, backoff,
                )
                time.sleep(backoff)
                continue
            logger.debug(
                "Semantic Scholar title search failed for %s", arxiv_id,
                exc_info=True,
            )
            return None
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))
                continue
            logger.debug(
                "Semantic Scholar title search failed for %s", arxiv_id,
                exc_info=True,
            )
            return None

    papers = (data.get("data") or [])
    if not papers:
        logger.info(
            "Semantic Scholar title search returned no results for: %.80s",
            title,
        )
        return None

    # Find the best match: prefer exact title match with year proximity.
    # Partial matches are inherently risky for common-phrase titles like
    # "Attention Is All You Need" and are only used as a last resort when
    # the word overlap is very high (>= 85%) AND the year is close.
    title_lower = title.lower().strip()
    title_words = set(title_lower.split())
    best_doi: Optional[str] = None
    best_score = 0.0  # 1.0 = exact title + same year, lower = worse

    for paper in papers:
        p_title = (paper.get("title") or "").lower().strip()
        p_doi = (paper.get("externalIds") or {}).get("DOI") or ""
        p_year = paper.get("year") or 0

        # Skip synthetic arXiv DOIs and papers without DOIs
        if not p_doi or p_doi.startswith(_ARXIV_DOI_PREFIX):
            continue

        # Score: exact title match = 1.0, partial = word overlap ratio,
        #   both multiplied by year proximity bonus (1.0 for same year,
        #   decaying by 0.1 per year difference).
        p_words = set(p_title.split())
        overlap = len(title_words & p_words) / max(1, len(title_words))

        # Reject papers with < 80% word overlap — too risky
        if overlap < 0.8:
            continue

        year_bonus = 1.0 if not p_year else max(
            0.5, 1.0 - 0.1 * abs(p_year - 2017)
        )
        score = overlap * year_bonus

        if score > best_score:
            best_score = score
            best_doi = p_doi

    if best_doi and best_score >= 0.85:
        logger.info(
            "Found publisher DOI %s via Semantic Scholar title search "
            "(score=%.2f) for arXiv %s",
            best_doi, best_score, arxiv_id,
        )
        return best_doi

    logger.info(
        "No suitable DOI found via Semantic Scholar title search for %.80s "
        "(checked %d results).",
        title, len(papers),
    )
    return None


def _fetch_arxiv_title(arxiv_id: str) -> Optional[str]:
    """Fetch the paper title for an arXiv ID from the arXiv API.

    Returns the title string, or ``None`` if the API call fails.
    """
    import xml.etree.ElementTree as ET

    aid = arxiv_id.strip().split("v", 1)[0]
    url = (
        f"http://export.arxiv.org/api/query"
        f"?id_list={aid}&max_results=1"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "paper-search-mcp/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        root = ET.fromstring(raw)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
        }
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                title = " ".join(title_el.text.strip().split())
                logger.info(
                    "Fetched title for arXiv %s: %s", arxiv_id, title[:80]
                )
                return title
        logger.warning("No title found in arXiv API response for %s", arxiv_id)
        return None
    except Exception:
        logger.warning(
            "arXiv API title fetch failed for %s", arxiv_id, exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Module-level helper: DOI-based publisher download
# (used by server.py IEEE/ACM download routing)
# ---------------------------------------------------------------------------


async def _download_publisher_by_doi(
    doi: str,
    save_path: str,
    timeout: int = 120,
    title: str = "",
) -> Dict[str, Any]:
    """Download a publisher PDF by DOI, bypassing paper_key cache lookup.

    This is the direct entry point for sources like IEEE/ACM whose native
    download raises NotImplementedError but whose search results carry a
    publisher DOI that scansci-pdf can use.

    Args:
        doi: Publisher DOI (e.g. ``"10.1109/..."``).
        save_path: Directory where the publisher PDF is saved.
        timeout: Maximum seconds (default 120).
        title: Optional paper title for logging.

    Returns:
        A dict with ``status`` and, on success, ``publisher_pdf``.
    """
    resolved_save = resolve_save_path(save_path)
    publisher_save_dir = Path(resolved_save) / "publish"
    publisher_save_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale .doi_index.json
    doi_index = publisher_save_dir / ".doi_index.json"
    if doi_index.exists():
        try:
            doi_index.unlink()
        except OSError:
            pass

    # 1. Get scansci-pdf client
    client = await _get_scansci_client()
    if client is None:
        return {
            "status": "unavailable",
            "doi": doi,
            "message": (
                "scansci-pdf is not available. "
                "Install: pip install scansci-pdf[cloakbrowser]"
            ),
            "detail": _scansci_error,
        }

    # 2. Ensure environment is ready
    await _ensure_scansci_ready(client)

    # 3. Call scansci-pdf
    MAX_RETRIES = 3
    result = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with client:
                result = await asyncio.wait_for(
                    client.call_tool(
                        "scansci_pdf_smart_download",
                        {
                            "identifier": doi,
                            "output_dir": str(publisher_save_dir),
                        },
                    ),
                    timeout=timeout,
                )
            break
        except asyncio.TimeoutError:
            if attempt == MAX_RETRIES:
                return {
                    "status": "timeout",
                    "doi": doi,
                    "timeout_seconds": timeout,
                    "attempts": attempt,
                    "message": (
                        f"scansci-pdf did not complete within {timeout}s."
                    ),
                }
            await asyncio.sleep(2 ** (attempt - 1))
        except Exception as exc:
            exc_name = type(exc).__name__
            if (
                _CONNECTION_LOST_EXCEPTIONS
                and isinstance(exc, _CONNECTION_LOST_EXCEPTIONS)
            ) or exc_name in _CONNECTION_LOST_NAMES:
                # Reset dead client
                global _scansci_client, _scansci_setup_done
                global _keys_injected, _tor_available
                async with _scansci_lock:
                    _scansci_client = None
                    _scansci_setup_done = False
                    _keys_injected = False
                    _tor_available = None
                if attempt < MAX_RETRIES:
                    client = await _get_scansci_client()
                    if client is None:
                        return {
                            "status": "error",
                            "doi": doi,
                            "message": "scansci-pdf process exited and could not be restarted.",
                        }
                    await _ensure_scansci_ready(client)
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
            logger.exception("scansci-pdf call failed for DOI %s", doi)
            return {
                "status": "error",
                "doi": doi,
                "message": f"scansci-pdf call failed: {exc}",
            }

    # 4. Parse response
    try:
        scansci_data = _parse_call_tool_result(result)
        if not scansci_data.get("success"):
            return {
                "status": "download_failed",
                "doi": doi,
                "message": (
                    "scansci-pdf was unable to download the publisher version "
                    "for this DOI."
                ),
                "scansci_result": scansci_data,
            }

        pdf_path = scansci_data.get("file", "")
        download_source = scansci_data.get("source", "unknown")

        if not pdf_path or not _is_valid_pdf_file(pdf_path):
            return {
                "status": "invalid_pdf",
                "doi": doi,
                "message": "scansci-pdf returned a path that is not a valid PDF.",
            }

        pdf_sha256 = await asyncio.to_thread(sha256_file, pdf_path)
        try:
            pdf_bytes = os.path.getsize(os.path.expanduser(pdf_path))
        except OSError:
            pdf_bytes = 0

        # Schedule background metadata enrichment from CrossRef
        if doi and not doi.startswith(_ARXIV_DOI_PREFIX):
            paper_key_hint = f"doi_{doi.replace('/', '_')}"
            asyncio.create_task(
                _enrich_download_metadata(
                    paper_key=paper_key_hint,
                    doi=doi,
                    pdf_path=pdf_path,
                    title_hint=title or "",
                )
            )

        return {
            "status": "ok",
            "doi": doi,
            "publisher_pdf": pdf_path,
            "download_source": download_source,
            "pdf_sha256": pdf_sha256,
            "pdf_bytes": pdf_bytes,
            "title": title or "",
            "metadata_enrichment": "scheduled" if doi else "skipped",
        }
    finally:
        # Cleanup stale cache
        _cleanup_doi_index(str(publisher_save_dir))


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


# ── Metadata enrichment ────────────────────────────────────────────


def _fetch_crossref_metadata(doi: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Fetch enriched paper metadata from CrossRef API by DOI.

    Returns a dict with ``title``, ``authors`` (list of str), ``year``,
    ``venue`` (journal/conference name), ``publisher``, and ``type``.
    Returns an empty dict on any error (non-blocking).
    """
    import json as _json

    if not doi or doi.startswith(_ARXIV_DOI_PREFIX):
        return {}

    url = f"https://api.crossref.org/works/{doi}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "paper-search-mcp/1.0 (mailto:paper-search@example.org)",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.debug("CrossRef metadata fetch failed for DOI %s", doi, exc_info=True)
        return {}

    msg = data.get("message") if isinstance(data, dict) else {}
    if not isinstance(msg, dict):
        return {}

    # ── Title ──
    title = ""
    title_list = msg.get("title") or []
    if title_list:
        title = str(title_list[0]).strip()

    # ── Authors ──
    authors: List[str] = []
    for author in msg.get("author") or []:
        if isinstance(author, dict):
            family = author.get("family", "")
            given = author.get("given", "")
            name = f"{given} {family}".strip()
            if name:
                authors.append(name)

    # ── Year ──
    year = ""
    issued = msg.get("issued")
    if isinstance(issued, dict):
        date_parts = issued.get("date-parts") or []
        if date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            try:
                year = str(int(date_parts[0][0]))
            except (TypeError, ValueError):
                pass

    # ── Venue ──
    venue = ""
    container = msg.get("container-title") or []
    if container:
        venue = str(container[0]).strip()
    # Short name fallback
    if not venue:
        short_container = msg.get("short-container-title") or []
        if short_container:
            venue = str(short_container[0]).strip()

    # ── Publisher ──
    publisher = str(msg.get("publisher") or "").strip()

    # ── Type ──
    pub_type = str(msg.get("type") or "").strip()

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "publisher": publisher,
        "type": pub_type,
    }


async def _enrich_download_metadata(
    paper_key: str,
    doi: str,
    pdf_path: str,
    *,
    title_hint: str = "",
) -> Dict[str, Any]:
    """Enrich the cached paper metadata with CrossRef data after download.

    Called after a successful publisher PDF download to ensure the parsed
    paper index has complete title/authors/year/venue information rather
    than just the arXiv ID placeholder.

    Args:
        paper_key: Parsed-paper cache key.
        doi: Publisher DOI.
        pdf_path: Path to the downloaded publisher PDF.
        title_hint: Optional title already known (e.g. from cache).

    Returns:
        The enriched metadata dict, or an empty dict if enrichment failed.
    """
    # Only enrich if we have a real publisher DOI
    if not doi or doi.startswith(_ARXIV_DOI_PREFIX):
        return {}

    # Check existing metadata — skip if already has a real title
    try:
        existing = await asyncio.to_thread(read_parsed, paper_key, "metadata")
        if isinstance(existing, dict):
            existing_title = str(existing.get("title") or "").strip()
            # If we already have a proper title (not an arXiv ID placeholder),
            # and it doesn't look like "1706.03762", skip enrichment
            if (
                existing_title
                and not re.match(r"^\d{4}\.\d{4,5}$", existing_title)
                and not existing_title.startswith("arxiv_")
                and not existing_title == doi
            ):
                logger.debug(
                    "Metadata already enriched for %s, skipping.", paper_key
                )
                return existing
    except Exception:
        pass

    logger.info("Enriching metadata for %s from CrossRef DOI %s …", paper_key, doi)
    crossref_data = await asyncio.to_thread(_fetch_crossref_metadata, doi)

    if not crossref_data:
        return {}

    enriched_title = crossref_data.get("title") or title_hint or ""
    enriched_authors = crossref_data.get("authors") or []
    enriched_year = crossref_data.get("year") or ""
    enriched_venue = crossref_data.get("venue") or ""

    # Write enriched metadata to cache
    await asyncio.to_thread(
        record_download,
        pdf_path=pdf_path,
        paper_key_hint=paper_key,
        source="scansci-pdf",
        paper_id=doi,
        doi=doi,
        title=enriched_title or title_hint or paper_key,
        downloader="scansci-pdf",
        legal_status="publisher",
    )

    # Also update the parsed data with extra fields
    try:
        from ..cache import read_json, write_json, paper_dir

        directory = Path(paper_dir(paper_key))
        meta_path = directory / "metadata.json"
        existing_meta = read_json(meta_path, {})
        if isinstance(existing_meta, dict):
            if enriched_title:
                existing_meta["title"] = enriched_title
            if enriched_authors:
                existing_meta["authors"] = enriched_authors
            if enriched_year:
                existing_meta["year"] = enriched_year
            if enriched_venue:
                existing_meta["publication_venue"] = enriched_venue
            write_json(meta_path, existing_meta)
    except Exception:
        logger.debug("Failed to write enriched metadata", exc_info=True)

    result = {
        "title": enriched_title,
        "authors": enriched_authors,
        "year": enriched_year,
        "venue": enriched_venue,
        "publisher": crossref_data.get("publisher", ""),
        "type": crossref_data.get("type", ""),
    }
    logger.info(
        "Metadata enriched for %s: title='%s', authors=%d, year=%s, venue='%s'",
        paper_key,
        enriched_title[:80] if enriched_title else "(none)",
        len(enriched_authors),
        enriched_year or "(none)",
        enriched_venue or "(none)",
    )
    return result


# ── Sci-Hub connectivity diagnostics ───────────────────────────────


async def _diagnose_scihub_connectivity(
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Probe Sci-Hub domain availability and return a diagnostic report.

    Tests several well-known Sci-Hub domains with a lightweight HTTP HEAD
    request.  Does NOT attempt a full PDF download — only checks whether the
    domain is reachable and responding.

    Returns a dict with ``domains`` (per-domain status), ``reachable_count``,
    ``total``, and ``recommendation``.
    """
    SCIHUB_DOMAINS = [
        "https://sci-hub.se",
        "https://sci-hub.st",
        "https://sci-hub.ru",
        "https://sci-hub.ee",
        "https://sci-hub.wf",
        "https://sci-hub.yt",
        "https://sci-hub.is",
    ]

    async def _probe_one(url: str) -> Dict[str, Any]:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                },
            )
            # Use HEAD-like GET with a small Range to minimise data transfer
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {
                    "url": url,
                    "reachable": True,
                    "status_code": resp.getcode(),
                    "latency_ms": 0,  # Filled below
                }
        except urllib.error.HTTPError as exc:
            return {
                "url": url,
                "reachable": True,
                "status_code": exc.code,
                "note": f"HTTP {exc.code} — server responds but may block",
            }
        except urllib.error.URLError as exc:
            reason = str(exc.reason)[:120] if exc.reason else "unknown"
            return {
                "url": url,
                "reachable": False,
                "error": reason,
            }
        except Exception as exc:
            return {
                "url": url,
                "reachable": False,
                "error": str(exc)[:120],
            }

    # Probe in parallel
    probes = await asyncio.gather(
        *[_probe_one(domain) for domain in SCIHUB_DOMAINS],
        return_exceptions=True,
    )

    results: List[Dict[str, Any]] = []
    reachable = 0
    for probe in probes:
        if isinstance(probe, dict):
            results.append(probe)
            if probe.get("reachable"):
                reachable += 1

    recommendation = ""
    if reachable == 0:
        recommendation = (
            "No Sci-Hub domains are reachable from your network. "
            "Consider: 1) enabling Tor (already configured), "
            "2) using a VPN, or 3) configuring DNS over HTTPS "
            "(e.g. set system DNS to 1.1.1.1 or 8.8.8.8)."
        )
    elif reachable <= 2:
        recommendation = (
            f"Only {reachable}/{len(SCIHUB_DOMAINS)} Sci-Hub domains are "
            "reachable. Sci-Hub availability is degraded — publisher-direct "
            "sources (ElsevierAPI, CrossrefPage) are preferred."
        )
    else:
        recommendation = (
            f"{reachable}/{len(SCIHUB_DOMAINS)} Sci-Hub domains reachable. "
            "Sci-Hub fallback is available."
        )

    return {
        "domains": results,
        "reachable_count": reachable,
        "total": len(SCIHUB_DOMAINS),
        "recommendation": recommendation,
    }


# ── Source health advice ───────────────────────────────────────────


def _build_source_health_advice(scores: Dict[str, Any]) -> Dict[str, Any]:
    """Interpret scansci-pdf source scores into actionable advice.

    Categorises sources by health tier and provides recommendations for
    which sources to prefer or avoid.

    Args:
        scores: The parsed result of ``scansci_pdf_source_scores``.

    Returns:
        A dict with ``preferred``, ``healthy``, ``degraded``, ``avoid``
        source lists and a ``summary`` message.
    """
    sources = scores.get("sources") or scores.get("results") or {}
    if not isinstance(sources, dict):
        return {}

    preferred: List[str] = []
    healthy: List[str] = []
    degraded: List[str] = []
    avoid: List[str] = []

    for name, info in sources.items():
        if not isinstance(info, dict):
            continue
        success_rate = float(info.get("success_rate") or info.get("ema_success_rate") or 0)
        latency = float(info.get("avg_latency") or info.get("ema_latency") or 999)

        if success_rate >= 0.7:
            if latency < 5.0:
                preferred.append(name)
            else:
                healthy.append(name)
        elif success_rate >= 0.3:
            degraded.append(name)
        elif success_rate > 0:
            avoid.append(name)
        # Sources with 0% success rate are silently excluded (never recommended)

    summary_parts: List[str] = []
    if preferred:
        summary_parts.append(
            f"Preferred ({len(preferred)}): {', '.join(preferred)}"
        )
    if healthy:
        summary_parts.append(
            f"Healthy ({len(healthy)}): {', '.join(healthy)}"
        )
    if degraded:
        summary_parts.append(
            f"Degraded ({len(degraded)}): {', '.join(degraded)}"
        )
    if avoid:
        summary_parts.append(
            f"Avoid ({len(avoid)}): {', '.join(avoid)}"
        )

    return {
        "preferred": preferred,
        "healthy": healthy,
        "degraded": degraded,
        "avoid": avoid,
        "summary": " | ".join(summary_parts) if summary_parts else "No source data available.",
        "recommendation": (
            "Prioritise preferred sources for fastest downloads. "
            "Degraded sources may time out. "
            "Avoid sources have very low success rates and may waste time."
        ),
    }


# ── shared core logic (single paper) ──────────────────────────────

def _resolve_publisher_save_dir(
    paper_key: str,
    save_path: str,
) -> str:
    """Determine the save directory for publisher-version PDFs.

    Priority:
    1. If the paper has a known local PDF in the cache whose parent
       directory exists, save to ``{pdf_parent}/publish/``.
    2. Otherwise, save to ``{resolved_save_path}/publish/``.

    This keeps publisher PDFs organised separately from arXiv
    preprints and other cached artifacts.
    """
    # ── Check whether the parsed paper has a local PDF on disk ──
    try:
        metadata = read_parsed(paper_key, "metadata")
        if isinstance(metadata, dict):
            cached_pdf = str(metadata.get("pdf_path") or "").strip()
            if cached_pdf:
                cached_pdf_path = Path(cached_pdf).expanduser().resolve()
                parent = cached_pdf_path.parent
                if parent.exists():
                    publish_dir = parent / "publish"
                    publish_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(
                        "Publisher save dir (from local PDF): %s", publish_dir
                    )
                    return str(publish_dir)
    except Exception:
        pass

    # ── Fallback: save_path + publish subdirectory ─────────────
    base = resolve_save_path(save_path)
    publish_dir = Path(base) / "publish"
    publish_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Publisher save dir (default): %s", publish_dir)
    return str(publish_dir)


def register_publisher_tools(mcp):  # noqa: C901
    """Register publisher-version download tools on *mcp*."""

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
        # ── Resolve the base save path (used for arXiv auto-download
        #    and as fallback reference for the publisher subdirectory).
        resolved_save = resolve_save_path(save_path)

        # ── Determine where the publisher PDF should actually land.
        #    This is computed early so that when the paper is not yet in
        #    the cache we still get a sensible ``publish/`` subdirectory.
        publisher_save_dir = _resolve_publisher_save_dir(paper_key, save_path)

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

        # 2b. When we only have an arXiv ID (synthetic DOI fallback),
        #     try to find the real publisher DOI via a three-tier lookup:
        #       1. Semantic Scholar arXiv-ID→DOI direct mapping (fast)
        #       2. Semantic Scholar title search (fallback, with API key)
        #       3. arXiv API title → Semantic Scholar title search
        #
        #     When all DOI lookups fail, we pass skip_l0_arxiv=True to
        #     scansci-pdf, which bypasses the [L0] shortcut and lets
        #     Phase 1/2 publisher sources race for the paper directly.
        if identifier_type == "arxiv_id" and identifier:
            # Tier 1: direct arXiv-ID→DOI lookup
            real_doi = await asyncio.to_thread(
                _lookup_real_publisher_doi, identifier
            )

            # Tier 2: Semantic Scholar title search (when we have a title)
            if not real_doi and meta.get("title") and meta["title"] != identifier:
                logger.info(
                    "Direct DOI lookup failed for %s — "
                    "trying Semantic Scholar title search …",
                    paper_key,
                )
                real_doi = await asyncio.to_thread(
                    _lookup_doi_by_title_semsch,
                    meta["title"], identifier,
                )

            # Tier 3: fetch title from arXiv API, then title search
            if not real_doi:
                logger.info(
                    "Cached title search failed for %s — "
                    "fetching title from arXiv API …",
                    paper_key,
                )
                arxiv_title = await asyncio.to_thread(
                    _fetch_arxiv_title, identifier
                )
                if arxiv_title:
                    real_doi = await asyncio.to_thread(
                        _lookup_doi_by_title_semsch,
                        arxiv_title, identifier,
                    )

            if real_doi:
                identifier = real_doi
                identifier_type = "doi"
                logger.info(
                    "Publisher DOI resolved for %s: %s", paper_key, real_doi
                )
            else:
                logger.info(
                    "No publisher DOI found for %s (arXiv %s) — "
                    "will use skip_l0_arxiv=True to bypass "
                    "scansci-pdf [L0] arXiv shortcut and allow "
                    "publisher sources to race.",
                    paper_key, identifier,
                )

        # 2c. Dedup check: skip download if a valid publisher PDF already
        #     exists on disk for this paper (DOI-based matching).
        if identifier_type == "doi":
            _publish_dir = Path(publisher_save_dir)
            if _publish_dir.exists():
                # First check for a DOI-derived filename pattern
                _doi_slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", identifier)
                for _candidate in sorted(
                    _publish_dir.glob(f"*{_doi_slug}*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True
                ):
                    if _candidate.stat().st_size > 1024:
                        with open(_candidate, "rb") as _fh:
                            if _fh.read(4) == b"%PDF":
                                _cached_sha256 = await asyncio.to_thread(
                                    sha256_file, str(_candidate)
                                )
                                logger.info(
                                    "Publisher PDF already cached for DOI %s: %s",
                                    identifier, _candidate,
                                )
                                return {
                                    "status": "ok",
                                    "paper_key": paper_key,
                                    "identifier_sent": identifier,
                                    "identifier_type": identifier_type,
                                    "publisher_pdf": str(_candidate),
                                    "download_source": "cached",
                                    "downloader": "scansci-pdf",
                                    "pdf_sha256": _cached_sha256,
                                    "pdf_bytes": _candidate.stat().st_size,
                                    "was_parsed": False,
                                    "auto_downloaded_arxiv": auto_downloaded,
                                    "cached": True,
                                }
        # Also check for publisher PDFs by paper_key pattern
        _publish_dir = Path(publisher_save_dir)
        if _publish_dir.exists():
            _key_slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", paper_key)
            for _candidate in sorted(
                _publish_dir.glob(f"*{_key_slug}*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True
            ):
                if _candidate.stat().st_size > 1024:
                    with open(_candidate, "rb") as _fh:
                        if _fh.read(4) == b"%PDF":
                            _cached_sha256 = await asyncio.to_thread(
                                sha256_file, str(_candidate)
                            )
                            logger.info(
                                "Publisher PDF already cached for key %s: %s",
                                paper_key, _candidate,
                            )
                            return {
                                "status": "ok",
                                "paper_key": paper_key,
                                "identifier_sent": identifier,
                                "identifier_type": identifier_type,
                                "publisher_pdf": str(_candidate),
                                "download_source": "cached",
                                "downloader": "scansci-pdf",
                                "pdf_sha256": _cached_sha256,
                                "pdf_bytes": _candidate.stat().st_size,
                                "was_parsed": False,
                                "auto_downloaded_arxiv": auto_downloaded,
                                "cached": True,
                            }

        # 3. Lazy-init scansci-pdf client
        client = await _get_scansci_client()
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
        save_dir = publisher_save_dir
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

        # 6. Call scansci-pdf (with retry for transient failures — P2-1)
        MAX_RETRIES = 3
        result = None

        # When we only have an arXiv ID (publisher DOI lookup failed),
        # pass skip_l0_arxiv=True to bypass scansci-pdf's [L0] arXiv
        # direct shortcut and allow Phase 1/2 publisher sources to race.
        _skip_l0 = (identifier_type == "arxiv_id")
        if _skip_l0:
            logger.info(
                "Passing skip_l0_arxiv=True — allowing scansci-pdf "
                "Phase 1/2 publisher sources to race for %s",
                paper_key,
            )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with client:
                    result = await asyncio.wait_for(
                        client.call_tool(
                            "scansci_pdf_smart_download",
                            {
                                "identifier": identifier,
                                "output_dir": str(save_dir),
                                "skip_l0_arxiv": _skip_l0,
                                "skip_phase1_oa": _skip_l0,
                            },
                        ),
                        timeout=timeout,
                    )
                break  # Success — exit retry loop

            except asyncio.TimeoutError:
                if attempt == MAX_RETRIES:
                    _cleanup_doi_index(publisher_save_dir)
                    return {
                        "status": "timeout",
                        "paper_key": paper_key,
                        "identifier_sent": identifier,
                        "identifier_type": identifier_type,
                        "timeout_seconds": timeout,
                        "attempts": attempt,
                        "message": (
                            f"scansci-pdf did not complete within {timeout} s "
                            f"after {attempt} attempts. "
                            "Retry with a higher timeout for large papers."
                        ),
                    }
                backoff = 2 ** (attempt - 1)
                logger.warning(
                    "scansci-pdf timed out for %s (attempt %d/%d), "
                    "retrying in %ds …",
                    paper_key, attempt, MAX_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)

            except Exception as exc:
                # ── Connection-lost errors (subprocess crash) ──────
                # Use isinstance when the specific exception classes are
                # importable; fall back to name matching otherwise (P2-2).
                if (
                    _CONNECTION_LOST_EXCEPTIONS
                    and isinstance(exc, _CONNECTION_LOST_EXCEPTIONS)
                ) or type(exc).__name__ in _CONNECTION_LOST_NAMES:
                    logger.error(
                        "scansci-pdf connection lost for %s "
                        "(attempt %d/%d): %s",
                        paper_key, attempt, MAX_RETRIES, exc,
                    )
                    # Reset the dead client under lock (P3-1) so
                    # subsequent calls create a fresh connection.
                    async with _scansci_lock:
                        global _scansci_client, _scansci_setup_done
                        global _keys_injected, _tor_available
                        _scansci_client = None
                        _scansci_setup_done = False
                        _keys_injected = False
                        _tor_available = None

                    if attempt < MAX_RETRIES:
                        # Re-create the client for the next attempt
                        client = await _get_scansci_client()
                        if client is None:
                            _cleanup_doi_index(publisher_save_dir)
                            return {
                                "status": "error",
                                "paper_key": paper_key,
                                "identifier_sent": identifier,
                                "message": (
                                    "scansci-pdf process exited and could "
                                    "not be restarted."
                                ),
                                "detail": _scansci_error,
                            }
                        await _ensure_scansci_ready(client)
                        backoff = 2 ** (attempt - 1)
                        logger.warning(
                            "scansci-pdf client recreated for %s, "
                            "retrying in %ds (attempt %d/%d) …",
                            paper_key, backoff, attempt + 1, MAX_RETRIES,
                        )
                        await asyncio.sleep(backoff)
                        continue

                    # Last attempt exhausted — return error with hint
                    exc_name = type(exc).__name__
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
                    _cleanup_doi_index(publisher_save_dir)
                    return {
                        "status": "error",
                        "paper_key": paper_key,
                        "identifier_sent": identifier,
                        "message": (
                            f"scansci-pdf process exited unexpectedly "
                            f"({exc_name}) after {attempt} attempts. "
                            "This may happen when all download sources are "
                            "blocked by the network." + hint
                        ),
                        "error_type": exc_name,
                        "attempts": attempt,
                    }

                # ── Non-connection error — don't retry ────────────
                logger.exception(
                    "scansci-pdf call_tool failed for %s", paper_key
                )
                _cleanup_doi_index(publisher_save_dir)
                return {
                    "status": "error",
                    "paper_key": paper_key,
                    "identifier_sent": identifier,
                    "message": f"scansci-pdf call failed: {exc}",
                }

        # 7. Parse scansci-pdf response
        try:
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

            # 8b. Enrich metadata from CrossRef (background, non-blocking).
            #     Fires after the response is returned so the user sees
            #     the download result immediately.
            real_doi_for_enrich = meta.get("doi", "")
            if real_doi_for_enrich and not real_doi_for_enrich.startswith(_ARXIV_DOI_PREFIX):
                asyncio.create_task(
                    _enrich_download_metadata(
                        paper_key=paper_key,
                        doi=real_doi_for_enrich,
                        pdf_path=pdf_path,
                        title_hint=meta.get("title", ""),
                    )
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
                "metadata_enrichment": "scheduled" if real_doi_for_enrich else "skipped",
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
        finally:
            # Clean up scansci-pdf's .doi_index.json cache so it doesn't
            # short-circuit future downloads to stale arXiv preprints.
            _cleanup_doi_index(publisher_save_dir)

    # Expose the core download function at module level so other modules
    # (e.g. server.py IEEE/ACM download routing) can import and call it.
    import sys as _sys
    _sys.modules[__name__].download_one_publisher_version = _download_one_publisher_version

    # ══════════════════════════════════════════════════════════════════
    # Fast-fail helper: prevents long blocking auto-installs inside
    # MCP tool calls that would trigger the -32000 connection timeout.
    # ══════════════════════════════════════════════════════════════════

    def _ensure_publisher_available() -> Optional[Dict[str, Any]]:
        """Return ``None`` if scansci-pdf is ready, else a fast-fail error dict.

        Always tries a lightweight ``import scansci_pdf`` when the cached
        ``_scansci_importable`` flag is not already ``True``.  This keeps the
        fast-fail accurate even when the user installs scansci-pdf manually
        between two calls (the import is a ``sys.modules`` lookup once the
        package has been installed, so it is negligibly cheap).
        """
        global _scansci_importable, _scansci_install_attempted

        # Already known-good — proceed
        if _scansci_importable is True:
            return None

        # Re-check: maybe it was installed manually since last check
        try:
            import scansci_pdf  # noqa: F401, PLC0415
            _scansci_importable = True
            return None
        except ImportError:
            _scansci_importable = False

        # Not importable — return a clear action-required error
        if not _scansci_install_attempted:
            return {
                "status": "not_installed",
                "message": (
                    "scansci-pdf is not installed. "
                    "Run install_publisher_support first to install "
                    "all required publisher-access components, then "
                    "retry this download."
                ),
                "action_required": (
                    "uv sync --extra publisher  &&  playwright install chromium"
                ),
                "next_step": "Call install_publisher_support or run the command above manually.",
            }
        else:
            # Previously attempted — still broken
            return {
                "status": "not_installed",
                "message": (
                    "scansci-pdf could not be auto-installed. "
                    "Install manually: uv sync --extra publisher  &&  "
                    "playwright install chromium"
                ),
                "action_required": (
                    "uv sync --extra publisher  &&  playwright install chromium"
                ),
            }

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

        Connects to the external **scansci-pdf** MCP server to locate
        and download the publisher's version of record.  scansci-pdf
        races 13+ download sources — including anti-detection browser
        engine (CloakBrowser), Tor proxy, Sci-Hub, LibGen, Unpaywall,
        OpenAlex, and publisher-direct routes — to maximise the chance
        of retrieving the final published PDF.

        **Prerequisites**

        * scansci-pdf must be installed (call ``install_publisher_support``
          first if you get a ``"not_installed"`` response).
        * The paper must already be cached in paper-search-mcp
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
            ``"not_applicable"``, ``"not_installed"``, ``"unavailable"``,
            ``"download_failed"``, ``"timeout"``, ``"invalid_pdf"``,
            ``"error"``) and, on success, ``publisher_pdf``,
            ``download_source``, ``pdf_sha256``, etc.
        """
        # ── Fast-fail: scansci-pdf not installed ──────────────────
        fast_fail = _ensure_publisher_available()
        if fast_fail is not None:
            return fast_fail

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

        Uses scansci-pdf's native ``batch_download`` API which supports
        parallel downloads, resume, and per-source error categorisation.

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
        # ── Fast-fail: scansci-pdf not installed ──────────────────
        fast_fail = _ensure_publisher_available()
        if fast_fail is not None:
            return fast_fail

        # Resolve paper_keys
        raw = [k.strip() for k in (paper_keys or "").split(",") if k.strip()]
        if not raw:
            return {
                "status": "error",
                "message": "paper_keys is required (comma-separated list or 'all').",
            }

        if len(raw) == 1 and raw[0].lower() == "all":
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

        # ── Resolve identifiers for all papers ──────────────────────
        resolved_save = resolve_save_path(save_path)
        identifiers: List[str] = []
        paper_map: Dict[str, str] = {}  # identifier → paper_key
        has_arxiv_ids = False

        failed_results: List[Dict[str, Any]] = []

        for paper_key in raw:
            meta = await asyncio.to_thread(
                _extract_paper_metadata, read_parsed, paper_key
            )
            if meta is None:
                # ── Auto-download arXiv preprint to seed the cache ──────
                # P0 fix: batch tool was missing the arXiv auto-download
                # fallback that the single-paper tool has.  Papers not yet
                # in the parsed cache were silently skipped.
                arxiv_id = _extract_arxiv_id(paper_key, "", "")
                if not arxiv_id:
                    m = re.match(
                        r"^arxiv_(\d{4}\.\d{4,5})(?:v\d+)?$",
                        paper_key,
                        re.IGNORECASE,
                    )
                    if m:
                        arxiv_id = m.group(1)
                if arxiv_id:
                    logger.info(
                        "Batch: '%s' not in cache — auto-downloading arXiv %s …",
                        paper_key, arxiv_id,
                    )
                    pdf_path = await asyncio.to_thread(
                        _download_arxiv_preprint,
                        arxiv_id,
                        str(resolved_save),
                    )
                    if pdf_path:
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
                        meta = await asyncio.to_thread(
                            _extract_paper_metadata, read_parsed, paper_key
                        )
                if meta is None:
                    failed_results.append({
                        "paper_key": paper_key,
                        "status": "not_found",
                        "message": (
                            f"Paper '{paper_key}' not in cache and "
                            "auto-download failed.  Download the arXiv PDF "
                            "first, or use download_publisher_version for "
                            "single-paper auto-download."
                        ),
                    })
                    continue
            ident, id_type = _resolve_identifier(meta, _extract_arxiv_id)
            if not ident:
                failed_results.append({
                    "paper_key": paper_key,
                    "status": "not_found",
                    "message": (
                        f"No identifier (DOI or arXiv ID) could be "
                        f"resolved for paper '{paper_key}'."
                    ),
                })
                continue

            # Try to find a real publisher DOI
            if id_type == "arxiv_id":
                has_arxiv_ids = True
                real_doi = await asyncio.to_thread(
                    _lookup_real_publisher_doi, ident
                )
                if real_doi:
                    ident = real_doi
                else:
                    # Try title search
                    if meta.get("title") and meta["title"] != ident:
                        real_doi = await asyncio.to_thread(
                            _lookup_doi_by_title_semsch,
                            meta["title"], ident,
                        )
                    if not real_doi:
                        arxiv_title = await asyncio.to_thread(
                            _fetch_arxiv_title, ident
                        )
                        if arxiv_title:
                            real_doi = await asyncio.to_thread(
                                _lookup_doi_by_title_semsch,
                                arxiv_title, ident,
                            )
                    if real_doi:
                        ident = real_doi

            identifiers.append(ident)
            paper_map[ident] = paper_key

        if not identifiers:
            return {
                "status": "not_found",
                "message": "No valid identifiers found for the given paper keys.",
                "total": len(raw),
                "ok": 0,
                "failed": len(raw),
                "results": failed_results if failed_results else [
                    {"paper_key": pk, "status": "not_found",
                     "message": "Could not resolve identifier"}
                    for pk in raw
                ],
            }

        # ── Ensure scansci-pdf is ready ────────────────────────────
        client = await _get_scansci_client()
        if client is None:
            return {
                "status": "unavailable",
                "message": "scansci-pdf is not available.",
                "detail": _scansci_error,
            }
        setup_report = await _ensure_scansci_ready(client)

        # ── Call native batch API ──────────────────────────────────
        logger.info(
            "Batch download: %d papers via scansci_pdf_batch_download "
            "(skip_l0_arxiv=%s, skip_phase1_oa=%s)",
            len(identifiers), has_arxiv_ids, has_arxiv_ids,
        )
        try:
            async with client:
                batch_result_raw = await asyncio.wait_for(
                    client.call_tool(
                        "scansci_pdf_batch_download",
                        {
                            "identifiers": identifiers,
                            "output_dir": str(resolved_save),
                            "skip_l0_arxiv": has_arxiv_ids,
                            "skip_phase1_oa": has_arxiv_ids,
                            "resume": True,
                        },
                    ),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "message": (
                    f"Batch download timed out after {timeout}s. "
                    "Retry with a larger timeout or fewer papers."
                ),
                "total": len(raw),
                "ok": 0,
                "failed": len(raw),
                "results": [],
            }
        except Exception as exc:
            logger.exception("Batch download failed")
            return {
                "status": "error",
                "message": f"Batch download failed: {exc}",
                "total": len(raw),
                "ok": 0,
                "failed": len(raw),
                "results": [],
            }

        batch_data = _parse_call_tool_result(batch_result_raw)

        # ── Map results back to paper keys ─────────────────────────
        ok_count = 0
        failed_count = 0
        results: List[Dict[str, Any]] = []

        for item in batch_data.get("results", []):
            ident = item.get("identifier", "")
            paper_key = paper_map.get(ident, ident)
            if item.get("success"):
                ok_count += 1
                results.append({
                    "status": "ok",
                    "paper_key": paper_key,
                    "publisher_pdf": item.get("file", ""),
                    "download_source": item.get("source", "unknown"),
                    "identifier_sent": ident,
                })
                # Record in cache
                try:
                    await asyncio.to_thread(
                        record_download,
                        pdf_path=item.get("file", ""),
                        paper_key_hint=paper_key,
                        source="scansci-pdf-batch",
                        paper_id=ident,
                        doi=ident if ident.startswith("10.") else "",
                        title=paper_key,
                        downloader="scansci-pdf",
                        legal_status="publisher",
                    )
                except Exception:
                    pass
            else:
                failed_count += 1
                results.append({
                    "status": "download_failed",
                    "paper_key": paper_key,
                    "identifier_sent": ident,
                    "message": item.get("error", "unknown error"),
                    "error_type": item.get("error_type", ""),
                })

        # ── Prepend any per-paper failures from the identifier-resolution
        #     phase (cache-miss with failed auto-download, no identifier, …)
        if failed_results:
            results = failed_results + results

        return {
            "status": "ok" if (ok_count > 0 or failed_results) else "not_found",
            "total": len(raw),
            "ok": ok_count,
            "failed": failed_count + len(failed_results),
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
                "scansci-pdf is not installed. "
                "Call install_publisher_support to install it "
                "(async, non-blocking), or run manually: "
                "uv sync --extra publisher  &&  playwright install chromium"
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
        client = await _get_scansci_client()
        if client is None:
            report["client_available"] = False
            report["client_error"] = _scansci_error
            return report
        report["client_available"] = True

        # 4. Run health check + source scores inside scansci-pdf
        try:
            async with client:
                result = await client.call_tool(
                    "scansci_pdf_health_check", {"detailed": True}
                )
                report["health"] = _parse_call_tool_result(result)

                # 5. Fetch adaptive source scores (EMA-based success rates
                #    and latencies) so users can see which download sources
                #    are currently healthy.
                try:
                    scores_result = await client.call_tool(
                        "scansci_pdf_source_scores", {}
                    )
                    parsed_scores = _parse_call_tool_result(scores_result)
                    if parsed_scores:
                        report["source_scores"] = parsed_scores
                        # ── Interpret scores for actionable advice ──
                        advice = _build_source_health_advice(parsed_scores)
                        if advice:
                            report["source_health_advice"] = advice
                except Exception:
                    logger.debug(
                        "scansci_pdf_source_scores unavailable",
                        exc_info=True,
                    )
        except Exception as exc:
            report["health"] = {"error": str(exc)}

        # 6. Sci-Hub connectivity diagnostic (non-blocking probe)
        try:
            scihub_report = await _diagnose_scihub_connectivity(timeout=8.0)
            report["scihub"] = scihub_report
        except Exception:
            logger.debug("Sci-Hub diagnostic skipped", exc_info=True)

        return report

    # ══════════════════════════════════════════════════════════════════
    # Tool: install_publisher_support  (explicit async install)
    # ══════════════════════════════════════════════════════════════════

    @mcp.tool()
    async def install_publisher_support(
        install_playwright_browser: bool = True,
    ) -> Dict[str, Any]:
        """Install (or verify) the scansci-pdf publisher-download toolchain.

        Uses **async, non-blocking** subprocess calls so the MCP connection
        stays alive during installation — no ``-32000 Connection closed``
        timeouts.

        Call this tool once before the first use of
        ``download_publisher_version`` or ``batch_download_publisher_versions``.
        It is safe to call multiple times — subsequent calls detect existing
        installations and return quickly.

        Args:
            install_playwright_browser: If ``True`` (default), also install
                the Playwright Chromium browser (~182 MB) needed by
                CloakBrowser.  Set to ``False`` to skip this step.

        Returns:
            A dict with ``status``, per-component install results, and
            a summary of what is now available.
        """
        global _scansci_install_attempted, _scansci_importable
        from datetime import datetime as _install_start_dt

        start_time = _install_start_dt.utcnow()
        steps: List[Dict[str, Any]] = []

        # ── Step 0: Quick check — already fully installed? ──────────
        if _scansci_importable is True:
            components = _detect_publisher_components()
            missing = [
                k for k, v in components.items()
                if not v.get("available")
            ]
            # Also check Playwright if cloakbrowser is present
            pw_missing = False
            if (
                components.get("cloakbrowser", {}).get("available")
                and install_playwright_browser
            ):
                pw = _check_playwright_browser()
                pw_missing = not pw.get("chromium_browser")
            if not missing and not pw_missing:
                return {
                    "status": "already_installed",
                    "message": (
                        "scansci-pdf and all publisher-access components "
                        "are already installed and ready."
                    ),
                    "components": {
                        k: v.get("available") for k, v in components.items()
                    },
                    "steps": [],
                }

        # ── Step 1: Install/verify scansci-pdf base ────────────────
        steps.append({
            "phase": "scansci_pdf_base",
            "status": "started",
            "message": "Installing scansci-pdf[cloakbrowser] (async, non-blocking) …",
        })
        logger.info("install_publisher_support: starting async scansci-pdf install …")
        base_ok = await _auto_install_scansci_pdf_async()
        steps[-1]["status"] = "ok" if base_ok else "failed"
        steps[-1]["result"] = base_ok

        if not base_ok:
            return {
                "status": "install_failed",
                "message": (
                    "scansci-pdf base package could not be installed. "
                    "Check your network connection and try again, or "
                    "install manually: uv sync --extra publisher"
                ),
                "steps": steps,
                "elapsed_seconds": (
                    _install_start_dt.utcnow() - start_time
                ).total_seconds(),
            }

        # ── Step 2: Install missing optional components ────────────
        components = _detect_publisher_components()
        missing = [
            k for k, v in components.items()
            if not v.get("available")
        ]
        if missing:
            steps.append({
                "phase": "optional_components",
                "status": "started",
                "missing": missing,
                "message": f"Installing {len(missing)} missing component(s): {', '.join(missing)} …",
            })
            logger.info(
                "install_publisher_support: installing %d missing component(s): %s",
                len(missing), ", ".join(missing),
            )
            install_results = await _install_missing_components_async(missing)
            steps[-1]["status"] = "ok"
            steps[-1]["results"] = install_results
            still_missing = [k for k, v in install_results.items() if not v]
            if still_missing:
                steps[-1]["still_missing"] = still_missing
                steps[-1]["hint"] = (
                    "pip install "
                    + " ".join(
                        _PUBLISHER_COMPONENT_PIP_TARGETS.get(m, m)
                        for m in still_missing
                    )
                )
        else:
            steps.append({
                "phase": "optional_components",
                "status": "skipped",
                "message": "All optional components already installed.",
            })

        # ── Step 3: Playwright Chromium browser ────────────────────
        if install_playwright_browser:
            pw_status = _check_playwright_browser()
            if not pw_status.get("chromium_browser"):
                steps.append({
                    "phase": "playwright_chromium",
                    "status": "started",
                    "message": (
                        "Installing Playwright Chromium browser "
                        "(~182 MB, async, non-blocking) …"
                    ),
                })
                logger.info(
                    "install_publisher_support: installing Playwright Chromium …"
                )
                pw_ok = await _install_playwright_chromium_async()
                steps[-1]["status"] = "ok" if pw_ok else "failed"
                steps[-1]["result"] = pw_ok
                if pw_ok:
                    logger.info("Playwright Chromium installed ✓")
                else:
                    logger.warning(
                        "Playwright Chromium install failed. "
                        "CloakBrowser will not work. "
                        "Install manually: playwright install chromium"
                    )
                    steps[-1]["hint"] = (
                        "playwright install chromium"
                    )
            else:
                steps.append({
                    "phase": "playwright_chromium",
                    "status": "skipped",
                    "message": "Playwright Chromium already installed.",
                })

        # ── Build final report ─────────────────────────────────────
        elapsed = (_install_start_dt.utcnow() - start_time).total_seconds()
        components = _detect_publisher_components()
        all_ok = all(v.get("available") for v in components.values())

        pw_final = _check_playwright_browser()
        chromium_ok = bool(pw_final.get("chromium_browser"))

        return {
            "status": "ok",
            "message": (
                "Publisher support installed successfully."
                if all_ok
                else "Publisher support partially installed — "
                       "some components are still missing."
            ),
            "scansci_pdf_importable": _scansci_importable,
            "components": {
                k: v.get("available") for k, v in components.items()
            },
            "playwright_chromium": chromium_ok,
            "steps": steps,
            "elapsed_seconds": elapsed,
            "ready_for_download": all_ok and chromium_ok,
        }
