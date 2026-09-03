"""Tests for diagnostic host detection and capability-based UI routing."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from paper_search_mcp.engine.parse import (
    _selection_surface_policy,
    _selection_ui_mode,
    _selection_ui_should_open,
)
from paper_search_mcp.utils import (
    MCP_APPS_EXTENSION_ID,
    MCP_APPS_HTML_MIME,
    _notify_vscode_companion,
    client_supports_elicitation_url,
    client_supports_mcp_apps,
    detect_host,
    inspect_mcp_client,
    open_url_in_host_result,
    vscode_binary,
)


@pytest.fixture(autouse=True)
def _clear_detect_host_cache():
    detect_host.cache_clear()
    yield
    detect_host.cache_clear()


def _ctx(capabilities=None, *, name="test-client", request_meta=None):
    client_params = SimpleNamespace(
        capabilities=capabilities or {},
        protocolVersion="2025-06-18",
        clientInfo={"name": name, "version": "1.0"},
    )
    request_context = SimpleNamespace(
        meta=request_meta,
        request=SimpleNamespace(
            params=SimpleNamespace(meta=request_meta, _meta=request_meta)
        ),
    )
    return SimpleNamespace(
        session=SimpleNamespace(client_params=client_params),
        request_context=request_context,
    )


def _apps_capabilities(location="extensions"):
    return {
        location: {
            MCP_APPS_EXTENSION_ID: {"mimeTypes": [MCP_APPS_HTML_MIME]}
        }
    }


def test_detect_codex_from_config_file():
    with tempfile.TemporaryDirectory() as tmp:
        codex_dir = Path(tmp) / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[model]\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            Path, "home", return_value=Path(tmp)
        ):
            assert detect_host() == "codex"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "claude-vscode"}, "claude_code_vscode"),
        ({"CLAUDECODE": "1", "TERM": "xterm"}, "claude_code_cli"),
        ({"CLAUDECODE": "1", "CLAUDE_CODE_DESKTOP": "1"}, "claude_code_desktop"),
        ({"CLAUDE_DESKTOP": "1"}, "claude_desktop"),
        ({"PAPER_SEARCH_MCP_CLIENT_HOST": "deepseek_harness"}, "dsh"),
        ({"PAPER_SEARCH_MCP_CLIENT_HOST": "zcode"}, "zcode"),
    ],
)
def test_detect_explicit_hosts(env, expected):
    with mock.patch.dict(os.environ, env, clear=True):
        assert detect_host() == expected


def test_dsh_and_zcode_markers_are_diagnostic_only():
    for env, expected in [({"DSH_VERSION": "1"}, "dsh"), ({"INTEGRATION_IDE": "ZCode"}, "zcode")]:
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            Path, "home", side_effect=RuntimeError("no home")
        ):
            detect_host.cache_clear()
            assert detect_host() == expected
            assert _selection_surface_policy(force_open=True)["surface"] == "browser"


def test_legacy_host_capability_helpers_fail_closed_without_context():
    from paper_search_mcp.utils import host_mcp_apps_confirmed, host_supports_mcp_apps_widget

    assert host_supports_mcp_apps_widget() is False
    assert host_mcp_apps_confirmed() is False


def test_vscode_binary_from_env_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "code").write_text("", encoding="utf-8")
        with mock.patch.dict(os.environ, {"VSCODE_CWD": tmp}, clear=True):
            assert vscode_binary() == str(bin_dir / "code")


@pytest.mark.parametrize("location", ["extensions", "experimental"])
def test_mcp_apps_requires_explicit_mime_capability(location):
    assert client_supports_mcp_apps(_apps_capabilities(location)) is True
    assert client_supports_mcp_apps(
        {location: {MCP_APPS_EXTENSION_ID: {"mimeTypes": ["text/html"]}}}
    ) is False
    assert client_supports_mcp_apps({}) is False


def test_elicitation_url_empty_mapping_is_a_capability():
    assert client_supports_elicitation_url({"elicitation": {"url": {}}}) is True
    assert client_supports_elicitation_url({"elicitation": {"form": {}}}) is False


def test_inspect_mcp_client_reads_initialize_capabilities():
    inspected = inspect_mcp_client(_ctx(_apps_capabilities(), name="zcode"))
    assert inspected["source"] == "initialize"
    assert inspected["client_info"]["name"] == "zcode"
    assert client_supports_mcp_apps(inspected["capabilities"]) is True


def test_request_meta_capabilities_override_initialize_values():
    meta = {
        "io.modelcontextprotocol/clientCapabilities": _apps_capabilities()
    }
    inspected = inspect_mcp_client(_ctx({}, request_meta=meta))
    assert inspected["source"] == "request_meta"
    assert client_supports_mcp_apps(inspected["capabilities"]) is True


def test_explicit_apps_capability_selects_t1_regardless_of_client_name():
    for name in ("dsh", "zcode", "unknown-client"):
        policy = _selection_surface_policy(ctx=_ctx(_apps_capabilities(), name=name))
        assert policy["surface"] == "mcp_app"
        assert policy["app_widget_supported"] is True
        assert policy["detected_host"] == name


def test_client_name_without_capability_selects_browser_t2():
    for name in ("codex", "claude_desktop", "dsh", "zcode"):
        policy = _selection_surface_policy(ctx=_ctx({}, name=name), force_open=True)
        assert policy["surface"] == "browser"
        assert policy["app_widget_supported"] is False
        assert policy["fallback_tool"] == "open_paper_selection_page"
        assert policy["local_browser_should_open"] is False


def test_ui_mode_overrides_do_not_grant_apps_or_open_gui():
    with mock.patch.dict(
        os.environ, {"PAPER_SEARCH_MCP_SELECTION_UI_MODE": "app_only"}, clear=True
    ):
        assert _selection_ui_mode() == "app_only"
        assert _selection_surface_policy(ctx=_ctx({}))["surface"] == "browser"
        assert _selection_ui_should_open(force_open=True) is False

    with mock.patch.dict(
        os.environ, {"PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser"}, clear=True
    ):
        assert _selection_surface_policy(ctx=_ctx({}))["surface"] == "browser"
        assert _selection_ui_should_open(force_open=True) is True

    with mock.patch.dict(
        os.environ, {"PAPER_SEARCH_MCP_SELECTION_UI_MODE": "off"}, clear=True
    ):
        assert _selection_surface_policy(ctx=_ctx({}))["surface"] == "ui_disabled"


def test_server_side_url_opening_is_disabled():
    url = "http://127.0.0.1:64901/paper-selection/test"
    with mock.patch("paper_search_mcp.utils._notify_vscode_companion") as notify, mock.patch(
        "paper_search_mcp.utils._open_url_with_system_browser"
    ) as browser, mock.patch("paper_search_mcp.utils._open_url_with_vscode_open_url") as vscode_open:
        result = open_url_in_host_result(url)
    assert result["opened"] is False
    assert result["method"] == "client_open_link"
    assert result["error"] == "server_side_open_disabled"
    notify.assert_not_called()
    browser.assert_not_called()
    vscode_open.assert_not_called()


def test_legacy_companion_ipc_hook_fails_closed():
    assert _notify_vscode_companion("http://127.0.0.1/test") is False


def test_ui_disabled_is_an_explicit_surface_state():
    with mock.patch.dict(
        os.environ, {"PAPER_SEARCH_MCP_SELECTION_UI_MODE": "off"}, clear=True
    ):
        policy = _selection_surface_policy(ctx=_ctx({}))
        assert policy["surface"] == "ui_disabled"
        assert policy["fallback_tool"] == ""
