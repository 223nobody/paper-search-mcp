"""Tests for host environment detection in paper_search_mcp.utils."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from paper_search_mcp.utils import (
    _notify_vscode_companion,
    detect_host,
    host_is_claude_code,
    host_is_codex,
    host_is_vscode,
    host_supports_mcp_apps_widget,
    open_url_in_host_result,
    vscode_binary,
)


# ══════════════════════════════════════════════════════════════
# Fixtures to control the detection cache
# ══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clear_detect_host_cache():
    """Each test gets a fresh host detection result."""
    detect_host.cache_clear()
    yield
    detect_host.cache_clear()


# ══════════════════════════════════════════════════════════════
# Codex detection (disk-based: ~/.codex/config.toml)
# ══════════════════════════════════════════════════════════════


def test_detect_codex_from_config_file():
    """When ~/.codex/config.toml exists, return 'codex'."""
    with tempfile.TemporaryDirectory() as tmp:
        codex_dir = Path(tmp) / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[model]\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                assert detect_host() == "codex"
                assert host_is_codex() is True
                assert host_supports_mcp_apps_widget() is True
                assert host_is_vscode() is False
                assert host_is_claude_code() is False


# ══════════════════════════════════════════════════════════════
# Claude Code detection (env-var based, takes priority)
# ══════════════════════════════════════════════════════════════


def test_detect_claude_code_vscode():
    """CLAUDECODE=1 + claude-vscode entrypoint → claude_code_vscode."""
    env = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "claude-vscode",
        "VSCODE_PID": "12345",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert detect_host() == "claude_code_vscode"
        assert host_is_codex() is False
        assert host_supports_mcp_apps_widget() is False
        assert host_is_vscode() is True
        assert host_is_claude_code() is True


def test_detect_claude_code_cli():
    """CLAUDECODE=1 alone → claude_code_cli."""
    env = {"CLAUDECODE": "1"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert detect_host() == "claude_code_cli"
        assert host_is_codex() is False
        assert host_supports_mcp_apps_widget() is False
        assert host_is_vscode() is False
        assert host_is_claude_code() is True


def test_claude_code_takes_priority_over_codex_config():
    """CLAUDECODE=1 should win even if ~/.codex/config.toml exists."""
    with tempfile.TemporaryDirectory() as tmp:
        codex_dir = Path(tmp) / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[model]\n")

        env = {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "claude-vscode"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                # Runtime env var takes priority over disk check
                assert detect_host() == "claude_code_vscode"
                assert host_is_codex() is False


# ══════════════════════════════════════════════════════════════
# Claude Desktop detection
# ══════════════════════════════════════════════════════════════


def test_detect_claude_desktop():
    """CLAUDE_DESKTOP env var → claude_desktop."""
    env = {"CLAUDE_DESKTOP": "1"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert detect_host() == "claude_desktop"
        assert host_supports_mcp_apps_widget() is True


# ══════════════════════════════════════════════════════════════
# Generic VS Code detection
# ══════════════════════════════════════════════════════════════


def test_detect_vscode_generic():
    """VSCODE_PID alone without known AI agent markers."""
    env = {"VSCODE_PID": "99999"}
    _keep_home(env)
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(Path, "home", return_value=Path(tmp)):
            with mock.patch.dict(os.environ, env, clear=True):
                assert detect_host() == "vscode_generic"
                assert host_supports_mcp_apps_widget() is False
                assert host_is_vscode() is True
                assert host_is_claude_code() is False


def test_detect_codex_vscode_takes_priority_over_codex_config():
    """Codex IDE extension in VS Code should keep localhost fallback behavior."""
    with tempfile.TemporaryDirectory() as tmp:
        codex_dir = Path(tmp) / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[model]\n")

        env = {
            "VSCODE_PID": "24680",
            "PATH": rf"{tmp}\.vscode\extensions\openai.chatgpt-1.0.0\bin",
        }
        _keep_home(env)
        with mock.patch.object(Path, "home", return_value=Path(tmp)):
            with mock.patch.dict(os.environ, env, clear=True):
                assert detect_host() == "codex_vscode"
                assert host_is_codex() is False
                assert host_supports_mcp_apps_widget() is False
                assert host_is_vscode() is True


def test_explicit_client_host_can_force_codex_desktop():
    """Desktop users can explicitly force MCP Apps support if auto-detect is thin."""
    env = {
        "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
        "VSCODE_PID": "24680",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert detect_host() == "codex"
        assert host_is_codex() is True
        assert host_supports_mcp_apps_widget() is True


def _keep_home(env: dict) -> None:
    """Preserve the home-directory env var so Path.home() works."""
    for key in ("HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE"):
        val = os.environ.get(key)
        if val:
            env.setdefault(key, val)


# ══════════════════════════════════════════════════════════════
# Unknown / fallback
# ══════════════════════════════════════════════════════════════


def test_detect_unknown():
    """No markers at all → unknown."""
    with mock.patch.dict(os.environ, {}, clear=True):
        # Ensure no .codex dir exists at the fake home
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                assert detect_host() == "unknown"
                assert host_supports_mcp_apps_widget() is False
                assert host_is_vscode() is False
                assert host_is_claude_code() is False


# ══════════════════════════════════════════════════════════════
# vscode_binary detection
# ══════════════════════════════════════════════════════════════


def test_vscode_binary_returns_string():
    """vscode_binary() always returns a string (empty if not found)."""
    result = vscode_binary()
    assert isinstance(result, str)


def test_vscode_binary_from_env_cwd():
    """When VSCODE_CWD is set, prefer binary from that directory."""
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "code").write_text("")

        env = {"VSCODE_CWD": tmp}
        with mock.patch.dict(os.environ, env, clear=True):
            result = vscode_binary()
            assert result == str(bin_dir / "code")


def test_codex_vscode_opens_http_url_with_companion_ipc_without_cli_command():
    env = {"PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode"}
    with mock.patch.dict(os.environ, env, clear=True):
        detect_host.cache_clear()
        with mock.patch(
            "paper_search_mcp.utils._notify_vscode_companion",
            return_value=True,
        ) as notify_mock, mock.patch(
            "paper_search_mcp.utils._open_url_with_system_browser",
            return_value=True,
        ) as browser_mock, mock.patch("subprocess.run") as run_mock:
            result = open_url_in_host_result(
                "http://127.0.0.1:64901/paper-selection/test"
            )

    assert result["opened"] is True
    assert result["method"] == "vscode_companion_ipc"
    notify_mock.assert_called_once()
    browser_mock.assert_not_called()
    run_mock.assert_not_called()


def test_vscode_companion_notification_uses_explicit_ipc_payload():
    with mock.patch(
        "paper_search_mcp.ui.vscode_bridge._write_named_pipe",
        return_value=True,
    ) as pipe_mock, mock.patch(
        "paper_search_mcp.ui.vscode_bridge.notify_companion_extension",
        return_value=True,
    ) as pending_file_mock:
        opened = _notify_vscode_companion(
            "http://127.0.0.1:64901/paper-selection/test"
        )

    assert opened is True
    pipe_mock.assert_called_once_with(
        {
            "action": "open_selection_page",
            "params": {"url": "http://127.0.0.1:64901/paper-selection/test"},
        }
    )
    pending_file_mock.assert_not_called()


def test_codex_vscode_http_url_falls_back_to_system_browser_not_command_arg():
    env = {"PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode"}
    with mock.patch.dict(os.environ, env, clear=True):
        detect_host.cache_clear()
        with mock.patch(
            "paper_search_mcp.utils._notify_vscode_companion",
            return_value=False,
        ) as notify_mock, mock.patch(
            "paper_search_mcp.utils._open_url_with_system_browser",
            return_value=True,
        ) as browser_mock, mock.patch("subprocess.run") as run_mock:
            result = open_url_in_host_result(
                "http://127.0.0.1:64901/paper-selection/test"
            )

    assert result["opened"] is True
    assert result["method"] == "system_browser"
    notify_mock.assert_called_once()
    browser_mock.assert_called_once()
    run_mock.assert_not_called()


def test_codex_vscode_last_resort_uses_nonblocking_open_url():
    env = {"PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode"}
    with mock.patch.dict(os.environ, env, clear=True):
        detect_host.cache_clear()
        with mock.patch(
            "paper_search_mcp.utils._notify_vscode_companion",
            return_value=False,
        ), mock.patch(
            "paper_search_mcp.utils._open_url_with_system_browser",
            return_value=False,
        ) as browser_mock, mock.patch(
            "paper_search_mcp.utils.vscode_binary",
            return_value="code.cmd",
        ), mock.patch("subprocess.Popen") as popen_mock, mock.patch(
            "subprocess.run"
        ) as run_mock:
            result = open_url_in_host_result(
                "http://127.0.0.1:64901/paper-selection/test"
            )

    assert result["opened"] is True
    assert result["method"] == "vscode_open_url"
    browser_mock.assert_called_once()
    popen_mock.assert_called_once()
    args = popen_mock.call_args.args[0]
    assert args == [
        "code.cmd",
        "--open-url",
        "http://127.0.0.1:64901/paper-selection/test",
    ]
    assert "paper-search-companion.openSelector" not in args
    run_mock.assert_not_called()


# ══════════════════════════════════════════════════════════════
# UI mode selection integration tests
# ══════════════════════════════════════════════════════════════

class TestSelectionUiMode:
    """Test that _selection_ui_mode respects host detection."""

    def test_app_only_for_codex(self):
        """When host is codex, auto-detect switches to app_only."""
        env = {}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, env, clear=True),
        ):
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            (codex_dir / "config.toml").write_text("")

            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                detect_host.cache_clear()
                from paper_search_mcp.engine.parse import (
                    _selection_ui_mode,
                    _selection_ui_should_open,
                )

                mode = _selection_ui_mode()
                assert mode == "app_only", f"Expected app_only, got {mode}"
                # Browser should NOT open, even when force_open=True
                assert _selection_ui_should_open(force_open=True) is False
                assert _selection_ui_should_open(force_open=False) is False

    def test_auto_for_claude_code(self):
        """When host is claude_code, auto mode allows browser fallback."""
        env = {"CLAUDECODE": "1"}
        with mock.patch.dict(os.environ, env, clear=True):
            detect_host.cache_clear()
            from paper_search_mcp.engine.parse import (
                _selection_ui_mode,
                _selection_ui_should_open,
            )

            mode = _selection_ui_mode()
            assert mode == "auto", f"Expected auto, got {mode}"
            # Browser SHOULD open when force_open=True
            assert _selection_ui_should_open(force_open=True) is True
            # But not by default
            assert _selection_ui_should_open(force_open=False) is False

    def test_explicit_env_overrides_host(self):
        """User-set PAPER_SEARCH_MCP_SELECTION_UI_MODE always wins."""
        env = {"PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser"}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, env, clear=True),
        ):
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            (codex_dir / "config.toml").write_text("")

            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                detect_host.cache_clear()
                from paper_search_mcp.engine.parse import _selection_ui_mode

                # Explicit env setting beats auto-detection
                mode = _selection_ui_mode()
                assert mode == "local_browser"

    def test_selection_surface_policy_for_codex_desktop(self):
        env = {"PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop"}
        with mock.patch.dict(os.environ, env, clear=True):
            detect_host.cache_clear()
            from paper_search_mcp.engine.parse import _selection_surface_policy

            policy = _selection_surface_policy(force_open=True)
            assert policy["surface"] == "mcp_app"
            assert policy["detected_host"] == "codex"
            assert policy["app_widget_supported"] is True
            assert policy["local_browser_should_open"] is False
            detect_host.cache_clear()

    def test_selection_surface_policy_desktop_ignores_local_browser_override(self):
        env = {
            "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
            "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            detect_host.cache_clear()
            from paper_search_mcp.engine.parse import _selection_surface_policy

            policy = _selection_surface_policy(force_open=True)
            assert policy["surface"] == "mcp_app"
            assert policy["reason"] == "host_supports_mcp_app_sandbox"
            assert policy["local_browser_should_open"] is False
            detect_host.cache_clear()

    def test_selection_surface_policy_for_codex_vscode_force_open(self):
        env = {"PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode"}
        with mock.patch.dict(os.environ, env, clear=True):
            detect_host.cache_clear()
            from paper_search_mcp.engine.parse import _selection_surface_policy

            policy = _selection_surface_policy(force_open=True)
            assert policy["surface"] == "local_browser"
            assert policy["detected_host"] == "codex_vscode"
            assert policy["app_widget_supported"] is False
            detect_host.cache_clear()
