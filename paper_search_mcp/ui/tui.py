# paper_search_mcp/ui/tui.py
"""Terminal-based interactive paper selection UI for CLI-only hosts.

Uses the ``rich`` library to render an interactive checkbox list inside the
terminal.  Designed as a fallback when the MCP host cannot render MCP Apps
widgets and the user prefers not to open a browser.

Usage::

    from paper_search_mcp.ui.tui import render_paper_selection_tui
    indices = render_paper_selection_tui(papers, selection_token)
    # indices = "1,3,5" or "" (user cancelled)
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

# ── Rich detection ───────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt
    from rich import box
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False


# ══════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════


def render_paper_selection_tui(
    papers: List[Dict[str, Any]],
    selection_token: str = "",
    *,
    download_only: bool = False,
) -> str:
    """Render an interactive terminal paper selector.

    Returns the user's selected indices as a comma-separated string
    (e.g. ``"1,3,5"``), or an empty string if the user cancelled.

    Falls back to plain ``input()`` when ``rich`` is not installed **or**
    when the terminal encoding cannot handle Unicode (e.g. Windows GBK).
    """
    if not papers:
        print("No papers available for selection.", file=sys.stderr)
        return ""

    if HAS_RICH:
        try:
            return _rich_tui(papers, selection_token, download_only=download_only)
        except (UnicodeEncodeError, UnicodeDecodeError, OSError):
            # Terminal encoding doesn't support Unicode (e.g. GBK on Windows).
            # Retry with ASCII-safe Rich rendering before falling back.
            try:
                return _rich_tui(
                    papers, selection_token,
                    download_only=download_only,
                    _ascii_safe=True,
                )
            except (UnicodeEncodeError, UnicodeDecodeError, OSError):
                pass
    return _simple_stdin_selection(papers)


def is_tui_available() -> bool:
    """Return True when a usable TUI backend is present."""
    return HAS_RICH


# ══════════════════════════════════════════════════════════════
# Rich TUI
# ══════════════════════════════════════════════════════════════


def _rich_tui(
    papers: List[Dict[str, Any]],
    selection_token: str = "",
    *,
    download_only: bool = False,
    _ascii_safe: bool = False,
) -> str:
    console = Console(
        force_terminal=True,
        emoji=False if _ascii_safe else True,
        highlighter=False,
    )

    _print_header(console, len(papers), download_only=download_only)
    _print_paper_table(console, papers)

    console.print()
    console.print(
        Panel(
            "[bold cyan]How to select:[/]\n"
            "  • Enter numbers: [yellow]1,3,5[/] or [yellow]1-3[/]\n"
            "  • [yellow]all[/] → select every parse-ready paper\n"
            "  • Enter or [yellow]q[/] → cancel and skip selection",
            title="Selection",
            border_style="cyan",
        )
    )
    console.print()

    # Collect valid index range
    valid = {
        str(p.get("index", ""))
        for p in papers
        if isinstance(p, dict)
        and p.get("index") is not None
        and p.get("parse_ready") is not False
    }
    max_idx = max((int(v) for v in valid if v.isdigit()), default=len(papers))

    while True:
        try:
            raw = Prompt.ask("[bold green]Selection").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Cancelled.[/]")
            return ""

        if not raw or raw.lower() in {"q", "quit", "n", "no"}:
            console.print("[yellow]Selection skipped.[/]")
            return ""

        parsed = _parse_selection(raw, max_idx, valid)
        if parsed is None:
            console.print(
                f"[red]Invalid selection.[/] "
                f"Use numbers 1–{max_idx}, e.g. [yellow]1,3,5[/] or [yellow]1-3[/]."
            )
            continue

        if download_only:
            action = "download"
        else:
            action = "download and parse"
        preview = _preview_indices(parsed, papers)
        console.print(
            f"[bold]Will {action}:[/] {preview}"
        )
        confirm = Prompt.ask(
            "[bold green]Proceed?[/] [[Y]/n]",
            default="y",
        ).strip().lower()
        if confirm in {"", "y", "yes"}:
            return ",".join(str(i) for i in parsed)
        console.print("[yellow]Re-select:[/]")


def _print_header(console, total: int, *, download_only: bool = False) -> None:
    action = "download" if download_only else "download & parse with MinerU"
    console.print()
    console.print(
        Panel(
            f"[bold white]{total} papers ready[/] for selection\n"
            f"Action: [cyan]{action}[/]",
            title="Paper Search",
            border_style="blue",
        )
    )


def _print_paper_table(console, papers: List[Dict[str, Any]]) -> None:
    table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.ROUNDED,
        expand=True,
    )
    table.add_column("#", style="dim cyan", width=4, justify="right")
    table.add_column("Title", style="white", min_width=40, max_width=80)
    table.add_column("Source", style="green", width=14)
    table.add_column("Year", style="yellow", width=6)
    table.add_column("Ready", style="magenta", width=6, justify="center")

    for paper in papers:
        if not isinstance(paper, dict):
            continue
        idx = paper.get("index", "")
        title = (paper.get("title") or "Untitled")[:80]
        source = (paper.get("source") or "?")[:14]
        year = str(paper.get("year") or paper.get("published_date") or "")[:6]
        ready = (
            "[OK]" if paper.get("parse_ready") is not False
            else "[--]"
        )
        style = "" if paper.get("parse_ready") is not False else "dim"

        table.add_row(
            str(idx),
            title,
            source,
            year,
            ready,
            style=style,
        )

    console.print(table)


def _parse_selection(
    raw: str,
    max_idx: int,
    valid: set,
) -> Optional[List[int]]:
    """Parse user input into a sorted list of unique integer indices."""
    raw = raw.strip().lower()

    # "all" → every valid index
    if raw == "all":
        return sorted(int(v) for v in valid if v.isdigit())

    indices: List[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            range_parts = part.split("-", 1)
            try:
                start = int(range_parts[0].strip())
                end = int(range_parts[1].strip())
                if start < 1 or end > max_idx or start > end:
                    return None
                for i in range(start, end + 1):
                    if str(i) not in valid:
                        return None
                    indices.append(i)
            except (ValueError, IndexError):
                return None
        else:
            try:
                i = int(part)
            except ValueError:
                return None
            if i < 1 or i > max_idx or str(i) not in valid:
                return None
            indices.append(i)

    if not indices:
        return None
    return sorted(set(indices))


def _preview_indices(indices: List[int], papers: List[Dict[str, Any]]) -> str:
    """Build a one-line preview of selected paper titles."""
    paper_map = {}
    for p in papers:
        if isinstance(p, dict) and isinstance(p.get("index"), int):
            paper_map[p["index"]] = (p.get("title") or "Untitled")[:50]
    parts = []
    for i in indices[:5]:
        title = paper_map.get(i, f"#{i}")
        parts.append(f"[yellow]#{i}[/] {title}")
    if len(indices) > 5:
        parts.append(f"... and {len(indices) - 5} more")
    return ", ".join(parts)


# ══════════════════════════════════════════════════════════════
# Plain stdin fallback (no Rich)
# ══════════════════════════════════════════════════════════════


def _simple_stdin_selection(papers: List[Dict[str, Any]]) -> str:
    """Plain-text paper list with stdin input (no rich dependency)."""
    print()
    print("=" * 60)
    print("  Paper Selection")
    print("=" * 60)

    for paper in papers:
        if not isinstance(paper, dict):
            continue
        idx = paper.get("index", "")
        title = paper.get("title", "Untitled")[:80]
        source = paper.get("source", "?")
        year = paper.get("year", paper.get("published_date", ""))
        ready = "OK" if paper.get("parse_ready") is not False else "--"
        print(f"  {idx:>2}. [{ready}] {title}")
        print(f"      {source}  {year}")

    print()
    print("  Enter numbers to select (e.g. 1,3,5 or 1-3),")
    print("  'all' for all, or Enter to cancel.")
    print()

    try:
        raw = input("Selection> ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return ""

    if not raw or raw.lower() in {"q", "quit", "n"}:
        return ""

    # Quick-parse to validate (reuse the Rich parser)
    max_idx = max(
        (int(p.get("index", 0)) for p in papers if isinstance(p, dict)),
        default=len(papers),
    )
    valid = {
        str(p.get("index", ""))
        for p in papers
        if isinstance(p, dict) and p.get("parse_ready") is not False
    }
    parsed = _parse_selection(raw, max_idx, valid)
    if parsed is None:
        print(f"Invalid selection. Use numbers 1–{max_idx}.")
        return ""

    return ",".join(str(i) for i in parsed)
