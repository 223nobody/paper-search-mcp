#!/usr/bin/env python3
"""
paper-search-mcp global MCP install script (cross-platform)

Registers paper-search-mcp into Claude Code's global MCP config
(~/.claude/mcp.json). After installation, MCP tools are available
regardless of which workspace is open.

Usage:
    python scripts/install-mcp-global.py              # install
    python scripts/install-mcp-global.py --uninstall  # uninstall
    python scripts/install-mcp-global.py --force      # force reinstall
    python scripts/install-mcp-global.py --dry-run    # preview changes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _p(*args, **kwargs) -> None:
    """Cross-platform safe print that handles Windows GBK encoding.

    On Windows terminals using GBK, printing emoji or other Unicode
    characters raises UnicodeEncodeError. Try UTF-8 first, fall back
    to ASCII-safe mode with replacement characters.
    """
    try:
        _ = print(*args, **kwargs)
    except UnicodeEncodeError:
        safe_args = tuple(
            str(a).encode("ascii", errors="replace").decode("ascii")
            if isinstance(a, str) else a
            for a in args
        )
        _ = print(*safe_args, **kwargs)


def repo_root() -> Path:
    """Detect the repo root (parent of this script's directory)."""
    return Path(__file__).resolve().parent.parent


def claude_config_path() -> Path:
    """Return the path to Claude Code's global MCP config."""
    home = Path(os.environ.get(
        "HOME", os.environ.get("USERPROFILE", Path.home())
    ))
    return home / ".claude" / "mcp.json"


def build_server_entry(repo_dir: str) -> dict:
    """Build the MCP server config entry for paper-search-mcp."""
    return {
        "paper-search-mcp": {
            "type": "stdio",
            "command": "uv",
            "args": [
                "run",
                "--directory",
                str(repo_dir),
                "-m",
                "paper_search_mcp.server",
            ],
            "env": {
                "PAPER_SEARCH_MCP_SEARCH_PROFILE": os.environ.get(
                    "PAPER_SEARCH_MCP_SEARCH_PROFILE", "pdf-cs"
                ),
                "PAPER_SEARCH_MCP_MINERU_MODE": os.environ.get(
                    "PAPER_SEARCH_MCP_MINERU_MODE", "auto"
                ),
            },
        }
    }


def read_config(path: Path) -> dict:
    """Read existing JSON config; return empty dict on failure."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, PermissionError, OSError):
        pass
    return {}


def has_paper_search(cfg: dict) -> bool:
    """Check whether paper-search-mcp is already in the config."""
    return "paper-search-mcp" in cfg.get("mcpServers", {})


def install(
    cfg_path: Path,
    repo: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> bool:
    cfg = read_config(cfg_path)
    mcp_servers = cfg.setdefault("mcpServers", {})

    if has_paper_search(cfg) and not force:
        _p("[SKIP] paper-search-mcp already registered.")
        _p(f"       Config: {cfg_path}")
        existing = mcp_servers["paper-search-mcp"]
        _p(f"       Current repo: {existing.get('args', [])[2] if 'args' in existing else '?'}")
        _p("       Use --force to update.")
        return True

    new_entry = build_server_entry(repo)
    if has_paper_search(cfg) and force:
        _p("[UPDATE] Overwriting existing paper-search-mcp config...")
    else:
        _p("[INSTALL] Registering paper-search-mcp to global MCP config...")

    if dry_run:
        _p(f"\n[DRY-RUN] Would write to {cfg_path}:")
        mcp_servers_dry = dict(mcp_servers)
        mcp_servers_dry["paper-search-mcp"] = new_entry["paper-search-mcp"]
        cfg_dry = {**cfg, "mcpServers": mcp_servers_dry}
        _p(json.dumps(cfg_dry, indent=2, ensure_ascii=False))
        return True

    mcp_servers["paper-search-mcp"] = new_entry["paper-search-mcp"]

    # Ensure parent directory exists
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup old config
    if cfg_path.exists():
        backup = cfg_path.with_suffix(".json.bak")
        try:
            backup.write_text(
                cfg_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")

    _p(f"[OK] paper-search-mcp registered globally.")
    _p(f"     Config: {cfg_path}")
    _p(f"     Repo:   {repo}")
    _p("")
    _p("[NEXT] Restart Claude Code, then these MCP tools will be available:")
    _p("  - search_papers")
    _p("  - download_with_fallback")
    _p("  - download_and_parse_selected_papers")
    _p("  - get_parse_job_status")
    _p("  - ... and more (see README)")
    return True


def uninstall(cfg_path: Path, *, dry_run: bool = False) -> bool:
    cfg = read_config(cfg_path)

    if not has_paper_search(cfg):
        _p("[SKIP] paper-search-mcp not found in global config.")
        return True

    if dry_run:
        _p(f"\n[DRY-RUN] Would remove paper-search-mcp from {cfg_path}:")
        del cfg["mcpServers"]["paper-search-mcp"]
        if not cfg["mcpServers"]:
            del cfg["mcpServers"]
        _p(json.dumps(cfg, indent=2, ensure_ascii=False))
        return True

    _p("[UNINSTALL] Removing paper-search-mcp from global config...")
    del cfg["mcpServers"]["paper-search-mcp"]
    if not cfg["mcpServers"]:
        del cfg["mcpServers"]

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")

    _p(f"[OK] paper-search-mcp removed from global config.")
    _p(f"     Config: {cfg_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install paper-search-mcp to Claude Code global MCP config",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove paper-search-mcp from global config",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing paper-search-mcp config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing",
    )
    args = parser.parse_args()

    cfg_path = claude_config_path()
    repo = str(repo_root())

    _p("=" * 55)
    _p("  paper-search-mcp Global MCP Installer")
    _p(f"  Repo:   {repo}")
    _p(f"  Target: {cfg_path}")
    _p("=" * 55)
    _p("")

    if args.uninstall:
        ok = uninstall(cfg_path, dry_run=args.dry_run)
    else:
        ok = install(cfg_path, repo, force=args.force, dry_run=args.dry_run)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
