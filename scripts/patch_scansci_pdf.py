#!/usr/bin/env python3
"""
Apply paper-search-mcp optimisations to an installed scansci-pdf package.

Usage::

    python scripts/patch_scansci_pdf.py          # patch current venv
    python scripts/patch_scansci_pdf.py --check   # verify patches are applied
    python scripts/patch_scansci_pdf.py --path /path/to/scansci_pdf  # custom path

Run this after ``pip install --upgrade scansci-pdf`` to re-apply the
patches that enable Phase 1 OA source skipping (``skip_phase1_oa``).
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Patch definitions — each is (file_relative_path, old_string, new_string)
# ---------------------------------------------------------------------------
PATCHES = [
    # ── Patch 1: _build_free_sources accepts skip_generic_oa ──────────
    (
        "sources/__init__.py",
        # old_string
        '''def _build_free_sources(doi: str, config: dict[str, Any]) -> list[tuple[Any, str]]:
    """Build Phase 1 sources: all free/OA/grey sources, sorted by adaptive score."""
    from .scoring import sort_sources

    publisher_fast = get_publisher_fast_sources(doi)
    _fast_names = {label for _, label in publisher_fast}

    extra_fast = []
    for fn, label in [
        (try_unpaywall, "Unpaywall"),
        (try_openalex_oa, "OpenAlexOA"),
        (try_semanticscholar, "SemanticScholar"),
    ]:
        if label not in _fast_names:
            extra_fast.append((fn, label))

    sources = publisher_fast + extra_fast
    sources += [(try_doaj, "DOAJ"), (try_crossref_page_scrape, "CrossrefPage")]
    sources += [(try_europepmc, "EuropePMC"), (try_core, "CORE"), (try_pmc, "PMC")]

    if config.get("openalex_api_key"):
        sources.append((try_openalex_content_api, "OpenAlexContent"))

    if config.get("scihub_enabled", False):
        sources.append((try_scibban, "SciBban"))
    sources.append((try_libgen, "LibGen"))
    if config.get("scihub_enabled", False):
        sources.append((try_scihub, "Sci-Hub"))

    return sort_sources(sources)''',
        # new_string
        '''def _build_free_sources(doi: str, config: dict[str, Any], *, skip_generic_oa: bool = False) -> list[tuple[Any, str]]:
    """Build Phase 1 sources: all free/OA/grey sources, sorted by adaptive score.

    Args:
        doi: Normalised DOI string.
        config: scansci-pdf configuration dict.
        skip_generic_oa: When True, exclude generic OA resolvers (Unpaywall,
            OpenAlex, SemanticScholar, etc.) that often redirect back to arXiv
            preprints.  Only publisher-specific fast sources are included.
            Use this when ``skip_l0_arxiv=True`` to ensure Phase 2 publisher
            browser strategies get a chance to run.
    """
    from .scoring import sort_sources

    publisher_fast = get_publisher_fast_sources(doi)
    _fast_names = {label for _, label in publisher_fast}

    if skip_generic_oa:
        # Only include publisher-specific fast sources (ElsevierAPI,
        # ScienceDirect, NatureDirect, etc.) -- skip all OA resolvers
        # that might redirect back to arXiv.
        sources = list(publisher_fast)
    else:
        extra_fast = []
        for fn, label in [
            (try_unpaywall, "Unpaywall"),
            (try_openalex_oa, "OpenAlexOA"),
            (try_semanticscholar, "SemanticScholar"),
        ]:
            if label not in _fast_names:
                extra_fast.append((fn, label))

        sources = publisher_fast + extra_fast
        sources += [(try_doaj, "DOAJ"), (try_crossref_page_scrape, "CrossrefPage")]
        sources += [(try_europepmc, "EuropePMC"), (try_core, "CORE"), (try_pmc, "PMC")]

        if config.get("openalex_api_key"):
            sources.append((try_openalex_content_api, "OpenAlexContent"))

        if config.get("scihub_enabled", False):
            sources.append((try_scibban, "SciBban"))
        sources.append((try_libgen, "LibGen"))
        if config.get("scihub_enabled", False):
            sources.append((try_scihub, "Sci-Hub"))

    if not sources:
        return []

    return sort_sources(sources)''',
    ),

    # ── Patch 2: download() accepts skip_phase1_oa ────────────────────
    (
        "sources/__init__.py",
        # old_string
        '''def download(
    identifier: str,
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_instsci: bool = False,
    bibtex: bool = False,
    rename: bool = True,
    skip_l0_arxiv: bool = False,
    _institutional: bool = True,
    _config: dict[str, Any] | None = None,
) -> dict[str, Any]:''',
        # new_string
        '''def download(
    identifier: str,
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_instsci: bool = False,
    bibtex: bool = False,
    rename: bool = True,
    skip_l0_arxiv: bool = False,
    skip_phase1_oa: bool = False,
    _institutional: bool = True,
    _config: dict[str, Any] | None = None,
) -> dict[str, Any]:''',
    ),

    # ── Patch 3: Phase 1 call site passes skip_generic_oa ─────────────
    (
        "sources/__init__.py",
        # old_string
        '''    # Phase 1: Free sources (OA + grey) — parallel race
    free_sources = _build_free_sources(doi, config)
    if free_sources:
        result = _run_tiers_parallel(
            [(free_sources, "Free", 15)], doi, target_dir, output_path, config, use_tor, 15
        )
        if result:
            return _finalize_result(result, identifier, doi, target_dir, config, rename=rename, bibtex=bibtex)''',
        # new_string
        '''    # Phase 1: Free sources (OA + grey) -- parallel race.
    # When skip_phase1_oa=True, only publisher-specific fast sources are
    # included; generic OA resolvers that redirect back to arXiv are excluded
    # so Phase 2 publisher browser strategies get a chance to run.
    free_sources = _build_free_sources(doi, config, skip_generic_oa=skip_phase1_oa)
    if free_sources:
        tier_label = "PublisherFast" if skip_phase1_oa else "Free"
        result = _run_tiers_parallel(
            [(free_sources, tier_label, 15)], doi, target_dir, output_path, config, use_tor, 15
        )
        if result:
            return _finalize_result(result, identifier, doi, target_dir, config, rename=rename, bibtex=bibtex)''',
    ),

    # ── Patch 4: batch_download() accepts skip_phase1_oa ───────────────
    (
        "sources/__init__.py",
        # old_string
        '''def batch_download(
    identifiers: list[str],
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_instsci: bool = False,
    skip_l0_arxiv: bool = False,
    progress_callback: Any = None,
    batch_id: str | None = None,
    resume: bool = True,
) -> dict[str, Any]:''',
        # new_string
        '''def batch_download(
    identifiers: list[str],
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_instsci: bool = False,
    skip_l0_arxiv: bool = False,
    skip_phase1_oa: bool = False,
    progress_callback: Any = None,
    batch_id: str | None = None,
    resume: bool = True,
) -> dict[str, Any]:''',
    ),

    # ── Patch 5: batch_download passes skip_phase1_oa to download() ───
    (
        "sources/__init__.py",
        # old_string
        '''        result = download(doi, output_dir, scihub_enabled=config.get("scihub_enabled", True), use_instsci=True, _institutional=True)''',
        # new_string
        '''        result = download(doi, output_dir, scihub_enabled=config.get("scihub_enabled", True), use_instsci=True, skip_l0_arxiv=skip_l0_arxiv, skip_phase1_oa=skip_phase1_oa, _institutional=True)''',
    ),

    # ── Patch 6: scansci_pdf_smart_download accepts skip_phase1_oa ────
    (
        "server.py",
        # old_string
        '''@mcp_app.tool()
def scansci_pdf_smart_download(
    identifier: str,
    output_dir: str | None = None,
    bibtex: bool = False,
    skip_l0_arxiv: bool = False,
) -> str:
    """Download a paper with zero configuration required.

    Automatically tries all available sources (OA, Sci-Hub, LibGen, WebVPN) with
    automatic Tor bypass when direct access fails. Just give a DOI --- everything else is handled.

    Args:
        identifier: DOI (e.g. 10.1038/nature12373), DOI URL, or arXiv ID (e.g. 2301.00001)
        output_dir: Override default output directory
        bibtex: Also return BibTeX citation for this paper
        skip_l0_arxiv: When True and identifier is an arXiv ID, skip the fast
            [L0] arXiv direct download and go through Phase 1/2 publisher
            sources instead.  Use this to prefer the publisher final version
            over the arXiv preprint.
    """
    result = download(
        identifier, output_dir,
        scihub_enabled=True,
        use_tor=True,
        use_instsci=True,
        bibtex=bibtex,
        skip_l0_arxiv=skip_l0_arxiv,
    )''',
        # new_string
        '''@mcp_app.tool()
def scansci_pdf_smart_download(
    identifier: str,
    output_dir: str | None = None,
    bibtex: bool = False,
    skip_l0_arxiv: bool = False,
    skip_phase1_oa: bool = False,
) -> str:
    """Download a paper with zero configuration required.

    Automatically tries all available sources (OA, Sci-Hub, LibGen, WebVPN) with
    automatic Tor bypass when direct access fails. Just give a DOI --- everything else is handled.

    Args:
        identifier: DOI (e.g. 10.1038/nature12373), DOI URL, or arXiv ID (e.g. 2301.00001)
        output_dir: Override default output directory
        bibtex: Also return BibTeX citation for this paper
        skip_l0_arxiv: When True and identifier is an arXiv ID, skip the fast
            [L0] arXiv direct download and go through Phase 1/2 publisher
            sources instead.  Use this to prefer the publisher final version
            over the arXiv preprint.
        skip_phase1_oa: When True alongside skip_l0_arxiv, also skip generic
            OA resolvers (Unpaywall, OpenAlex, etc.) in Phase 1, leaving only
            publisher-specific fast sources.  This ensures Phase 2 publisher
            browser strategies get a chance to run when OA sources would
            otherwise return the arXiv preprint.
    """
    result = download(
        identifier, output_dir,
        scihub_enabled=True,
        use_tor=True,
        use_instsci=True,
        bibtex=bibtex,
        skip_l0_arxiv=skip_l0_arxiv,
        skip_phase1_oa=skip_phase1_oa,
    )''',
    ),

    # ── Patch 7: scansci_pdf_batch_download accepts skip_phase1_oa ────
    (
        "server.py",
        # old_string
        '''def scansci_pdf_batch_download(
    identifiers: list[str],
    output_dir: str | None = None,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_instsci: bool = False,
    skip_l0_arxiv: bool = False,
    batch_id: str | None = None,
    resume: bool = True,
    ctx: Any = None,
) -> str:
    """Download multiple papers by DOI or arXiv ID.

    Args:
        identifiers: List of DOIs or arXiv IDs
        output_dir: Override default output directory
        scihub_enabled: Enable/disable Sci-Hub
        use_tor: Route Sci-Hub/LibGen through Tor
        use_instsci: Try WebVPN institutional proxy as last resort (requires prior login via scansci_pdf_instsci_login)
        skip_l0_arxiv: When True, skip [L0] arXiv shortcut for arXiv IDs so publisher sources can race.
        batch_id: Unique ID for this batch (auto-generated if omitted). Used for resume support.
        resume: Skip items completed in a previous run (default true). Set false to re-download all.
    """
    from .log import get_logger
    _log = get_logger()

    def _progress_report(current: int, total: int, identifier: str, result: dict[str, Any]) -> None:
        ok = result.get("success", False)
        src = result.get("source", "?")
        status = "OK" if ok else "FAIL"
        _log.info(f"   [{current}/{total}] {status} {src} {identifier}")
        if ctx and hasattr(ctx, "report_progress"):
            try:
                ctx.report_progress(current, total)
            except Exception:
                pass

    result = batch_download(
        identifiers, output_dir,
        scihub_enabled=scihub_enabled, use_tor=use_tor, use_instsci=use_instsci,
        skip_l0_arxiv=skip_l0_arxiv,
        batch_id=batch_id, resume=resume,
        progress_callback=_progress_report,
    )''',
        # new_string
        '''def scansci_pdf_batch_download(
    identifiers: list[str],
    output_dir: str | None = None,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_instsci: bool = False,
    skip_l0_arxiv: bool = False,
    skip_phase1_oa: bool = False,
    batch_id: str | None = None,
    resume: bool = True,
    ctx: Any = None,
) -> str:
    """Download multiple papers by DOI or arXiv ID.

    Args:
        identifiers: List of DOIs or arXiv IDs
        output_dir: Override default output directory
        scihub_enabled: Enable/disable Sci-Hub
        use_tor: Route Sci-Hub/LibGen through Tor
        use_instsci: Try WebVPN institutional proxy as last resort (requires prior login via scansci_pdf_instsci_login)
        skip_l0_arxiv: When True, skip [L0] arXiv shortcut for arXiv IDs so publisher sources can race.
        skip_phase1_oa: When True alongside skip_l0_arxiv, also skip generic OA resolvers in Phase 1.
        batch_id: Unique ID for this batch (auto-generated if omitted). Used for resume support.
        resume: Skip items completed in a previous run (default true). Set false to re-download all.
    """
    from .log import get_logger
    _log = get_logger()

    def _progress_report(current: int, total: int, identifier: str, result: dict[str, Any]) -> None:
        ok = result.get("success", False)
        src = result.get("source", "?")
        status = "OK" if ok else "FAIL"
        _log.info(f"   [{current}/{total}] {status} {src} {identifier}")
        if ctx and hasattr(ctx, "report_progress"):
            try:
                ctx.report_progress(current, total)
            except Exception:
                pass

    result = batch_download(
        identifiers, output_dir,
        scihub_enabled=scihub_enabled, use_tor=use_tor, use_instsci=use_instsci,
        skip_l0_arxiv=skip_l0_arxiv, skip_phase1_oa=skip_phase1_oa,
        batch_id=batch_id, resume=resume,
        progress_callback=_progress_report,
    )''',
    ),
]


def _find_scansci_pdf_root() -> Path | None:
    """Find the scansci-pdf package root in the current Python environment."""
    # Try importlib first
    try:
        spec = importlib.util.find_spec("scansci_pdf")
        if spec is not None and spec.origin is not None:
            return Path(spec.origin).parent
    except Exception:
        pass

    # Fallback: search common venv paths
    from pathlib import Path as _Path
    candidates = [
        _Path(__file__).resolve().parent.parent / ".venv" / "Lib" / "site-packages" / "scansci_pdf",
        _Path.cwd() / ".venv" / "Lib" / "site-packages" / "scansci_pdf",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def apply_patches(root: Path, *, dry_run: bool = False) -> dict[str, list[bool]]:
    """Apply all known patches to scansci-pdf under *root*.

    Returns a dict mapping each file path to a list of booleans (True =
    patch was applied, False = already present or not found).
    """
    results: dict[str, list[bool]] = {}

    for subpath, old, new in PATCHES:
        target = root / subpath
        if not target.exists():
            results.setdefault(subpath, []).append(False)
            print(f"  SKIP {subpath} — file not found")
            continue

        content = target.read_text(encoding="utf-8")
        if old not in content:
            # Check if the patch is already applied
            if new.split("\n", 1)[0] in content:
                results.setdefault(subpath, []).append(True)
                print(f"  OK   {subpath} — already patched")
            else:
                results.setdefault(subpath, []).append(False)
                print(f"  WARN {subpath} — base text not found (version mismatch?)")
            continue

        if not dry_run:
            new_content = content.replace(old, new, 1)
            target.write_text(new_content, encoding="utf-8")
        results.setdefault(subpath, []).append(True)
        print(f"  {'DRY ' if dry_run else 'PATCH'} {subpath}")

    return results


def check_patches(root: Path) -> bool:
    """Return True if all patches are applied."""
    all_ok = True
    for subpath, _old, new in PATCHES:
        target = root / subpath
        if not target.exists():
            print(f"  MISS {subpath} — file not found")
            all_ok = False
            continue

        content = target.read_text(encoding="utf-8")
        # Check for the new signature line
        new_sig = new.split("\n", 1)[0].strip()
        if new_sig in content:
            print(f"  OK   {subpath}")
        else:
            print(f"  MISS {subpath} — patch not applied")
            all_ok = False
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply paper-search-mcp optimisations to scansci-pdf",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Only check whether patches are applied (exit 1 if not).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be patched without writing files.",
    )
    parser.add_argument(
        "--path", type=str, default=None,
        help="Path to scansci-pdf package root (auto-detected if omitted).",
    )
    args = parser.parse_args()

    if args.path:
        root = Path(args.path)
    else:
        root = _find_scansci_pdf_root()

    if root is None:
        print("ERROR: Could not find scansci-pdf installation.")
        print("       Install it first: pip install scansci-pdf[cloakbrowser]")
        return 2

    if not root.exists():
        print(f"ERROR: Path does not exist: {root}")
        return 2

    print(f"scansci-pdf root: {root}\n")

    if args.check:
        ok = check_patches(root)
        return 0 if ok else 1

    results = apply_patches(root, dry_run=args.dry_run)

    applied = sum(
        1 for file_results in results.values() for r in file_results if r
    )
    total = len(PATCHES)
    print(f"\nApplied {applied}/{total} patches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
