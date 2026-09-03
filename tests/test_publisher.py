"""Tests for the scansci-pdf publisher-version integration (publisher.py).

Covers the 1.14.0 tool-surface migration: version gate, upstream call
mapping, hint passthrough, force/cache-clear semantics, and the four
institutional-channel passthrough tools.

The mock seam is ``paper_search_mcp.tools.publisher._get_scansci_client``:
every registered tool resolves it from module globals at call time, so
patching it swaps the whole scansci-pdf subprocess for a fake client.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp import FastMCP

from paper_search_mcp.tools import publisher as pub
from paper_search_mcp.widgets.response import unwrap_tool_result

# Test DOI used across cases (non-arXiv prefix so the 3-tier DOI lookup
# is skipped; identifier_type resolves to "doi" directly).
TEST_DOI = "10.1038/s41598-020-00001"
TEST_PAPER_KEY = "arxiv_1706_03762"


class FakeScansciClient:
    """Minimal stand-in for the fastmcp Client over scansci-pdf stdio."""

    def __init__(self, responses=None, default=None):
        self.calls = []  # (tool_name, args_dict)
        self.responses = responses or {}
        self.default_response = default or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, arguments=None):
        args = dict(arguments or {})
        self.calls.append((name, args))
        payload = self.responses.get(name, self.default_response)
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(payload))]
        )

    def called(self, name):
        return [a for n, a in self.calls if n == name]

    def call_names(self):
        return [n for n, _ in self.calls]


class PublisherToolTestCase(unittest.TestCase):
    """Shared fixture: fresh module globals + registered tools + fake cache."""

    def setUp(self):
        # Reset module-level lazy state so each test starts clean.
        pub._scansci_client = None
        pub._scansci_error = ""
        pub._scansci_setup_done = False
        pub._scansci_install_attempted = False
        pub._scansci_importable = True  # skip real import / auto-install
        pub._keys_injected = False
        pub._tor_available = None
        pub._scansci_version_ok = True  # version gate passes by default
        pub._component_status = {}
        # Fresh locks per test: each asyncio.run() creates a new loop and
        # a lock used in one loop cannot be awaited in another.
        pub._scansci_lock = asyncio.Lock()
        pub._publisher_login_lock = asyncio.Lock()

        self.tmp = tempfile.TemporaryDirectory()
        self.save_path = self.tmp.name
        self.seed_pdf = Path(self.save_path) / "seed.pdf"
        self.seed_pdf.write_bytes(b"%PDF-1.4 test seed")

        self.meta = {
            "source": "arxiv",
            "doi": TEST_DOI,
            "paper_id": "1706.03762",
            "title": "Attention Is All You Need",
            "pdf_path": str(self.seed_pdf),
        }

        self.mcp = FastMCP("test-publisher")
        pub.register_publisher_tools(self.mcp)

    def tearDown(self):
        self.tmp.cleanup()

    async def _get_tool(self, name):
        # fastmcp 3.x: get_tool() is a coroutine.
        return await self.mcp.get_tool(name)

    async def _run_tool(self, name, args):
        tool = await self._get_tool(name)
        result = await tool.run(args)
        return unwrap_tool_result(result)

    def run_tool(self, name, args):
        return asyncio.run(self._run_tool(name, args))

    def _fake_client(self, responses=None, default=None):
        return FakeScansciClient(responses=responses, default=default)

    def _patch_client(self, client):
        return patch(
            "paper_search_mcp.tools.publisher._get_scansci_client",
            new=AsyncMock(return_value=client),
        )

    def _standard_patches(self, client):
        """Common patch stack: fake client + no-op setup/cache/enrich/network.

        Returns a context manager so tests can use ``with``.
        """
        patchers = [
            self._patch_client(client),
            patch(
                "paper_search_mcp.tools.publisher._ensure_scansci_ready",
                new=AsyncMock(return_value={"setup": "already_done"}),
            ),
            patch(
                "paper_search_mcp.tools.publisher.read_parsed",
                side_effect=lambda key, fmt="metadata": self.meta,
            ),
            patch(
                "paper_search_mcp.tools.publisher.record_download",
                new=MagicMock(),
            ),
            patch(
                "paper_search_mcp.tools.publisher._enrich_download_metadata",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "paper_search_mcp.tools.publisher._diagnose_scihub_connectivity",
                new=AsyncMock(return_value={}),
            ),
        ]
        stack = contextlib.ExitStack()
        for p in patchers:
            stack.enter_context(p)
        return stack

    def _success_payload(self, **overrides):
        payload = {
            "success": True,
            "file": str(self.seed_pdf),
            "source": "unpaywall",
            "doi": TEST_DOI,
        }
        payload.update(overrides)
        return payload


# ────────────────────────────────────────────────────────────────────────
# Version gate
# ────────────────────────────────────────────────────────────────────────


class VersionGateTests(PublisherToolTestCase):
    def test_old_version_fails_fast_with_upgrade_hint(self):
        pub._scansci_version_ok = None  # force re-evaluation
        with patch("importlib.metadata.version", return_value="1.6.1"):
            result = self.run_tool(
                "download_publisher_version",
                {"paper_key": TEST_PAPER_KEY, "save_path": self.save_path},
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("1.14.0", result.get("upgrade_hint", ""))

    def test_old_version_blocks_client_creation(self):
        pub._scansci_version_ok = None
        with patch("importlib.metadata.version", return_value="1.6.1"):
            client = asyncio.run(pub._get_scansci_client())
        self.assertIsNone(client)
        self.assertIn("upgrade required", pub._scansci_error)
        self.assertIn("1.14.0", pub._scansci_error)

    def test_new_version_passes_gate(self):
        pub._scansci_version_ok = None
        with patch("importlib.metadata.version", return_value="1.14.0"):
            hint = pub._check_scansci_version()
        self.assertIsNone(hint)
        self.assertTrue(pub._scansci_version_ok)


# ────────────────────────────────────────────────────────────────────────
# download_publisher_version
# ────────────────────────────────────────────────────────────────────────


class DownloadToolTests(PublisherToolTestCase):
    def test_call_args_map_to_1_14_surface(self):
        client = self._fake_client(default=self._success_payload())
        with self._standard_patches(client):
            result = self.run_tool(
                "download_publisher_version",
                {
                    "paper_key": TEST_PAPER_KEY,
                    "save_path": self.save_path,
                    "bibtex": True,
                    "download_si": True,
                },
            )
        self.assertEqual(result["status"], "ok")
        self.assertIn("scansci_pdf_download", client.call_names())
        download_args = client.called("scansci_pdf_download")[0]
        self.assertEqual(download_args["identifier"], TEST_DOI)
        self.assertTrue(download_args["output_dir"].endswith("publish"))
        self.assertIs(download_args["bibtex"], True)
        self.assertIs(download_args["download_si"], True)
        # The removed 1.6.1 escape hatches must never be sent.
        self.assertNotIn("skip_l0_arxiv", download_args)
        self.assertNotIn("skip_phase1_oa", download_args)

    def test_arxiv_id_fallback_passes_arxiv_identifier(self):
        meta = dict(self.meta, doi="")  # no DOI → arxiv_id path
        client = self._fake_client(default=self._success_payload())
        patchers = [
            self._patch_client(client),
            patch(
                "paper_search_mcp.tools.publisher._ensure_scansci_ready",
                new=AsyncMock(return_value={"setup": "already_done"}),
            ),
            patch(
                "paper_search_mcp.tools.publisher.read_parsed",
                side_effect=lambda key, fmt="metadata": meta,
            ),
            patch(
                "paper_search_mcp.tools.publisher.record_download",
                new=MagicMock(),
            ),
            patch(
                "paper_search_mcp.tools.publisher._enrich_download_metadata",
                new=AsyncMock(return_value=None),
            ),
            # All three DOI-lookup tiers fail → keep the arXiv ID.
            patch(
                "paper_search_mcp.tools.publisher._lookup_real_publisher_doi",
                return_value=None,
            ),
            patch(
                "paper_search_mcp.tools.publisher._lookup_doi_by_title_semsch",
                return_value=None,
            ),
            patch(
                "paper_search_mcp.tools.publisher._fetch_arxiv_title",
                return_value=None,
            ),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(patch.stopall)

        result = self.run_tool(
            "download_publisher_version",
            {"paper_key": TEST_PAPER_KEY, "save_path": self.save_path},
        )
        self.assertEqual(result["status"], "ok")
        download_args = client.called("scansci_pdf_download")[0]
        self.assertEqual(download_args["identifier"], "1706.03762")

    def test_failure_passes_through_upstream_hints(self):
        payload = {
            "success": False,
            "error": "paywall",
            "error_type": "paywall",
            "action": "login_required",
            "agent_hint": "请运行 publisher_login",
            "hint": {"manual_url": "https://example.com"},
        }
        client = self._fake_client(default=payload)
        with self._standard_patches(client):
            result = self.run_tool(
                "download_publisher_version",
                {"paper_key": TEST_PAPER_KEY, "save_path": self.save_path},
            )
        self.assertEqual(result["status"], "download_failed")
        self.assertEqual(result["error_type"], "paywall")
        self.assertEqual(result["action"], "login_required")
        self.assertIn("publisher_login", result["agent_hint"])
        self.assertEqual(result["hint"]["manual_url"], "https://example.com")

    def test_cached_pdf_returns_without_upstream_call(self):
        publish_dir = Path(self.save_path) / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        cached = publish_dir / f"paper_{TEST_DOI.replace('/', '_')}.pdf"
        # > 1024 bytes — the dedup check ignores tiny files.
        cached.write_bytes(b"%PDF-1.4 " + b"x" * 2048)

        client = self._fake_client()
        with self._standard_patches(client):
            result = self.run_tool(
                "download_publisher_version",
                {"paper_key": TEST_PAPER_KEY, "save_path": self.save_path},
            )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result.get("cached"))
        self.assertEqual(client.call_names(), [])

    def test_force_clears_cache_and_skips_dedup(self):
        publish_dir = Path(self.save_path) / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        cached = publish_dir / f"paper_{TEST_DOI.replace('/', '_')}.pdf"
        # > 1024 bytes — the dedup check ignores tiny files.
        cached.write_bytes(b"%PDF-1.4 " + b"x" * 2048)

        client = self._fake_client(
            default=self._success_payload(),
            responses={"scansci_pdf_cache_clear": {"cleared": True}},
        )
        with self._standard_patches(client):
            result = self.run_tool(
                "download_publisher_version",
                {
                    "paper_key": TEST_PAPER_KEY,
                    "save_path": self.save_path,
                    "force": True,
                },
            )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("cached", False))
        names = client.call_names()
        self.assertEqual(names[0], "scansci_pdf_cache_clear")
        self.assertEqual(
            client.called("scansci_pdf_cache_clear")[0]["identifier"],
            TEST_DOI,
        )
        self.assertEqual(names[1], "scansci_pdf_download")


# ────────────────────────────────────────────────────────────────────────
# batch_download_publisher_versions
# ────────────────────────────────────────────────────────────────────────


class BatchToolTests(PublisherToolTestCase):
    def test_batch_call_args_map_to_1_14_surface(self):
        payload = {
            "results": [
                {
                    "identifier": TEST_DOI,
                    "success": True,
                    "file": str(self.seed_pdf),
                    "source": "unpaywall",
                },
                {
                    "identifier": TEST_DOI,
                    "success": False,
                    "error": "paywall",
                    "error_type": "paywall",
                    "agent_hint": "需要机构登录",
                },
            ]
        }
        client = self._fake_client(default=payload)
        with self._standard_patches(client):
            result = self.run_tool(
                "batch_download_publisher_versions",
                {
                    "paper_keys": TEST_PAPER_KEY,
                    "save_path": self.save_path,
                },
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total"], 1)
        batch_args = client.called("scansci_pdf_batch_download")[0]
        self.assertEqual(batch_args["identifiers"], [TEST_DOI])
        self.assertIs(batch_args["resume"], True)
        self.assertNotIn("skip_l0_arxiv", batch_args)
        self.assertNotIn("skip_phase1_oa", batch_args)
        # The failed duplicate maps back and carries the hint.
        failed = [r for r in result["results"] if r["status"] == "download_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["agent_hint"], "需要机构登录")

    def test_batch_timeout_reports_suggested_timeout(self):
        class SlowClient(FakeScansciClient):
            async def call_tool(self, name, arguments=None):
                self.calls.append((name, dict(arguments or {})))
                await asyncio.sleep(5)  # exceed the 1s timeout
                return SimpleNamespace(
                    content=[SimpleNamespace(text=json.dumps({}))]
                )

        client = SlowClient()
        with self._standard_patches(client):
            result = self.run_tool(
                "batch_download_publisher_versions",
                {
                    "paper_keys": TEST_PAPER_KEY,
                    "save_path": self.save_path,
                    "timeout": 1,
                },
            )
        self.assertEqual(result["status"], "timeout")
        self.assertIn("suggested_timeout", result)


# ────────────────────────────────────────────────────────────────────────
# New institutional-channel tools
# ────────────────────────────────────────────────────────────────────────


class NewToolsTests(PublisherToolTestCase):
    def test_publisher_schools_maps_args(self):
        client = self._fake_client(
            default={"universities": ["清华大学"]},
        )
        with self._standard_patches(client):
            result = self.run_tool(
                "publisher_schools",
                {"action": "search", "query": "北京"},
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scansci_result"]["universities"], ["清华大学"])
        args = client.called("scansci_pdf_schools")[0]
        self.assertEqual(args, {"action": "search", "query": "北京", "school": ""})

    def test_publisher_channel_status_maps_args(self):
        client = self._fake_client(default={"webvpn": {"status": "ok"}})
        with self._standard_patches(client):
            result = self.run_tool(
                "publisher_channel_status",
                {"kind": "carsi", "doi": ""},
            )
        self.assertEqual(result["status"], "ok")
        args = client.called("scansci_pdf_channel_status")[0]
        self.assertEqual(args, {"kind": "carsi", "doi": ""})

    def test_publisher_diagnostics_maps_args(self):
        client = self._fake_client(default={"healthy": True})
        with self._standard_patches(client):
            result = self.run_tool(
                "publisher_diagnostics",
                {"check": "network", "detailed": False},
            )
        self.assertEqual(result["status"], "ok")
        args = client.called("scansci_pdf_diagnostics")[0]
        self.assertEqual(args, {"check": "network", "detailed": False})

    def test_publisher_login_maps_args_and_returns_ok(self):
        client = self._fake_client(default={"logged_in": True})
        with self._standard_patches(client):
            result = self.run_tool(
                "publisher_login",
                {"kind": "webvpn", "identifier": "", "max_wait": 30},
            )
        self.assertEqual(result["status"], "ok")
        args = client.called("scansci_pdf_login")[0]
        self.assertEqual(args["kind"], "webvpn")
        self.assertEqual(args["max_wait"], 30)

    def test_publisher_login_rejects_concurrent_login(self):
        client = self._fake_client(default={"logged_in": True})

        async def slow_login(name, arguments=None):
            client.calls.append((name, dict(arguments or {})))
            await asyncio.sleep(0.2)
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({"logged_in": True}))]
            )

        client.call_tool = slow_login
        with self._standard_patches(client):
            async def fire_two():
                tool = await self.mcp.get_tool("publisher_login")
                # Run both logins concurrently: the first holds the login
                # lock inside slow_login while the second checks it.
                first_task = asyncio.create_task(
                    tool.run({"kind": "webvpn", "max_wait": 5})
                )
                await asyncio.sleep(0.1)  # first acquires the lock
                second_task = asyncio.create_task(
                    tool.run({"kind": "webvpn", "max_wait": 5})
                )
                r1 = unwrap_tool_result(await first_task)
                r2 = unwrap_tool_result(await second_task)
                return r1, r2

            r1, r2 = asyncio.run(fire_two())
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r2["status"], "busy")

    def test_new_tools_return_unavailable_without_client(self):
        with patch(
            "paper_search_mcp.tools.publisher._get_scansci_client",
            new=AsyncMock(return_value=None),
        ):
            for name, args in [
                ("publisher_login", {"kind": "webvpn"}),
                ("publisher_schools", {"action": "search"}),
                ("publisher_channel_status", {}),
                ("publisher_diagnostics", {}),
            ]:
                result = self.run_tool(name, args)
                self.assertEqual(result["status"], "unavailable", name)
                self.assertIn("detail", result, name)


# ────────────────────────────────────────────────────────────────────────
# check_publisher_setup
# ────────────────────────────────────────────────────────────────────────


class CheckSetupTests(PublisherToolTestCase):
    def test_degradation_when_diagnostics_fails(self):
        class BoomClient(FakeScansciClient):
            async def call_tool(self, name, arguments=None):
                raise RuntimeError("diagnostics boom")

        client = BoomClient()
        patchers = [
            self._patch_client(client),
            patch(
                "paper_search_mcp.tools.publisher._diagnose_scihub_connectivity",
                new=AsyncMock(return_value={}),
            ),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(patch.stopall)

        result = self.run_tool("check_publisher_setup", {})
        self.assertTrue(result["scansci_pdf_installed"])
        self.assertTrue(result["client_available"])
        self.assertIn("scansci_pdf_version", result)
        self.assertTrue(result["version_ok"])
        self.assertEqual(result["health"], {"error": "diagnostics boom"})

    def test_reports_version_mismatch(self):
        pub._scansci_version_ok = None
        client = self._fake_client(default={})
        patchers = [
            self._patch_client(client),
            patch("importlib.metadata.version", return_value="1.6.1"),
            patch(
                "paper_search_mcp.tools.publisher._diagnose_scihub_connectivity",
                new=AsyncMock(return_value={}),
            ),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(patch.stopall)

        result = self.run_tool("check_publisher_setup", {})
        self.assertTrue(result["scansci_pdf_installed"])
        self.assertEqual(result["scansci_pdf_version"], "1.6.1")
        self.assertFalse(result["version_ok"])
        self.assertIn("1.14.0", result.get("upgrade_hint", ""))


# ────────────────────────────────────────────────────────────────────────
# _ensure_scansci_ready call mapping
# ────────────────────────────────────────────────────────────────────────


class EnsureReadyMappingTests(PublisherToolTestCase):
    def test_setup_uses_only_1_14_tool_names(self):
        pub._scansci_setup_done = False
        pub._tor_available = False  # skip the extra-long first-setup timeout
        responses = {
            "scansci_pdf_diagnostics": {
                "status": {"tor": "running"},
                "summary": "ready",
            },
            "scansci_pdf_tor": {"running": True},
            "scansci_pdf_config": {"ok": True},
        }
        client = self._fake_client(responses=responses)

        # clear=True isolates get_env from the developer's real .env file
        # (only the key we set here may be injected).
        with patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "test@example.com"},
            clear=True,
        ):
            report = asyncio.run(pub._ensure_scansci_ready(client))

        names = client.call_names()
        # No legacy 1.6.1 tool names may ever be sent.
        for legacy in (
            "scansci_pdf_smart_download",
            "scansci_pdf_auto_setup",
            "scansci_pdf_tor_start",
            "scansci_pdf_config_set",
            "scansci_pdf_health_check",
            "scansci_pdf_source_scores",
        ):
            self.assertNotIn(legacy, names)

        self.assertIn("scansci_pdf_diagnostics", names)
        self.assertEqual(
            client.called("scansci_pdf_diagnostics")[0], {"check": "auto_setup"}
        )
        self.assertIn("scansci_pdf_tor", names)
        self.assertEqual(
            client.called("scansci_pdf_tor")[0], {"action": "start"}
        )
        config_calls = client.called("scansci_pdf_config")
        self.assertTrue(config_calls)
        self.assertIn(
            {"key": "email", "value": "test@example.com"}, config_calls
        )
        self.assertIn("keys_injected", report)
        self.assertIn("email", report["keys_injected"])


if __name__ == "__main__":
    unittest.main()
