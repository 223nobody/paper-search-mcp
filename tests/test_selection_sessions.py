import asyncio
import json
import os
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult

from paper_search_mcp import cache, server
from paper_search_mcp.engine.parse import dismiss_parse_prompt_state
from paper_search_mcp.widgets.response import unwrap_tool_result


def _local_page_confirmation_token(html: str) -> str:
    match = re.search(r"data-page=\"([^\"]+)\"", html)
    if not match:
        raise AssertionError("local selection page did not include data-page")
    data = json.loads(match.group(1).replace("&quot;", '"'))
    return str(data.get("confirmation_token") or "")


class TestSelectionSessions(unittest.TestCase):
    def test_download_selected_papers_tool_declares_checkbox_output_template(self):
        tools = asyncio.run(server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "download_selected_papers")

        self.assertEqual(tool.meta["ui"]["resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(tool.meta["ui/resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(tool.meta["openai/outputTemplate"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertTrue(tool.meta["openai/widgetAccessible"])

    def test_parse_selected_indices_accepts_text_all_and_ranges(self):
        self.assertEqual(server._parse_selected_indices("all", 3), [1, 2, 3])
        self.assertEqual(server._parse_selected_indices("1, 3", 4), [1, 3])
        self.assertEqual(server._parse_selected_indices("2-4", 5), [2, 3, 4])
        self.assertEqual(server._parse_selected_indices([1, 2, 2], 3), [1, 2])

        with self.assertRaises(ValueError):
            server._parse_selected_indices("0", 3)

    def test_elicitation_schema_uses_array_enum(self):
        schema = server._build_paper_selection_schema(["1. First", "2. Second"])
        selected = schema.model_json_schema()["properties"]["selected_papers"]
        self.assertEqual(selected["type"], "array")
        self.assertEqual(selected["items"]["enum"], ["1. First", "2. Second"])

    def test_parse_elicitation_selected_indices(self):
        selected = ["2. Second Paper [arxiv, 2026]", "1. First Paper [semantic, 2025]"]
        self.assertEqual(server._parse_elicitation_selected_indices(selected, 3), [2, 1])

    def test_search_papers_for_parsing_creates_numbered_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "scene aware skills",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": 1},
                "errors": {},
                "papers": [
                    {
                        "title": "Scene-Aware Skills",
                        "authors": "A. Researcher",
                        "published_date": "2026-01-02",
                        "source": "arxiv",
                        "paper_id": "2601.00001",
                        "doi": "",
                        "pdf_url": "https://example.org/paper.pdf",
                        "url": "https://example.org/paper",
                    }
                ],
                "total": 1,
                "raw_total": 1,
            }

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.search_papers",
                new=AsyncMock(return_value=fake_search_result),
            ):
                result = asyncio.run(
                    server.search_papers_for_parsing(
                        "scene aware skills",
                        max_results_per_source=1,
                        sources="arxiv",
                    )
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["papers"][0]["index"], 1)
            self.assertEqual(result["papers"][0]["reason"], "arxiv_pdf")

            loaded = cache.get_search_session(result["selection_token"], cache_dir=tmp)
            self.assertEqual(loaded["papers"][0]["title"], "Scene-Aware Skills")

    def test_arxiv_preprint_is_not_used_as_publication_venue(self):
        candidate = server._paper_parse_candidate(
            {
                "title": "Vision Agent Skill Paper",
                "source": "arxiv",
                "paper_id": "2601.00001",
                "venue": "arXiv preprint",
                "categories": ["cs.CV", "cs.AI"],
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                "url": "https://arxiv.org/abs/2601.00001",
            },
            1,
        )

        self.assertEqual(candidate["publication_venue"], "Computer Vision and Pattern Recognition")

    def test_arxiv_journal_ref_takes_precedence_over_category_venue(self):
        candidate = server._paper_parse_candidate(
            {
                "title": "Published Vision Paper",
                "source": "arxiv",
                "paper_id": "2601.00002",
                "categories": ["cs.CV"],
                "extra": {
                    "journal_ref": "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
                },
                "pdf_url": "https://arxiv.org/pdf/2601.00002",
                "url": "https://arxiv.org/abs/2601.00002",
            },
            1,
        )

        self.assertEqual(
            candidate["publication_venue"],
            "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
        )

    def test_crawl_papers_for_selection_over_ten_returns_app_session(self):
        papers = [
            {
                "title": f"Agent Skill Paper {index}",
                "source": "arxiv",
                "paper_id": f"2601.{index:05d}",
                "pdf_url": f"https://example.org/{index}.pdf",
            }
            for index in range(1, 12)
        ]
        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": len(papers)},
            "errors": {},
            "papers": papers,
            "total": len(papers),
            "raw_total": len(papers),
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
            },
        ), patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=fake_search_result),
        ):
            server.detect_host.cache_clear()
            result = asyncio.run(
                server.crawl_papers_for_selection(
                    "agent skill",
                    max_results_per_source=11,
                    sources="arxiv",
                )
            )
            payload = unwrap_tool_result(result)
            loaded = cache.get_search_session(payload["selection_token"], cache_dir=tmp)
            server.detect_host.cache_clear()

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(result.meta["ui"]["resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result.meta["ui/resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result.meta["openai/outputTemplate"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertTrue(result.meta["openai/widgetAccessible"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["total"], 11)
        self.assertEqual(len(payload["papers"]), 11)
        self.assertEqual(len(payload["numbered_fallback"]), 11)
        self.assertEqual(payload["app"]["render_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(payload["app"]["selection_token"], payload["selection_token"])
        self.assertEqual(payload["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertEqual(payload["app"]["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertEqual(payload["interaction"], "mcp_app")
        self.assertEqual(len(loaded["papers"]), 11)
        self.assertEqual(loaded["metadata"]["interaction"], "crawl_papers_for_selection")
        self.assertEqual(loaded["metadata"]["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)

    def test_crawl_papers_for_selection_requested_count_over_limit_requires_selection(self):
        papers = [
            {
                "title": f"Requested Agent Skill Paper {index}",
                "source": "arxiv",
                "paper_id": f"2602.{index:05d}",
                "pdf_url": f"https://example.org/requested-{index}.pdf",
            }
            for index in range(1, 15)
        ]
        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": len(papers)},
            "errors": {},
            "papers": papers,
            "total": len(papers),
            "raw_total": len(papers),
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
            },
        ), patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=fake_search_result),
        ):
            server.detect_host.cache_clear()
            result = asyncio.run(
                server.crawl_papers_for_selection(
                    "agent skill",
                    max_results_per_source=14,
                    sources="arxiv",
                    requested_count=12,
                )
            )
            payload = unwrap_tool_result(result)
            loaded = cache.get_search_session(payload["selection_token"], cache_dir=tmp)
            server.detect_host.cache_clear()

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(payload["status"], "selection_required")
        self.assertEqual(payload["requested_count"], 12)
        self.assertEqual(payload["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(payload["recommended_selected_indices"], "1-12")
        self.assertTrue(payload["parse_decision_required"])
        self.assertFalse(loaded["metadata"].get("large_batch_selection_satisfied", False))

    def test_codex_app_shortlist_keeps_session_full_and_skips_non_ready(self):
        papers = []
        for index in range(1, 61):
            paper = {
                "title": f"Agent Skill Candidate {index}",
                "source": "arxiv",
                "paper_id": f"2605.{index:05d}",
                "pdf_url": f"https://example.org/agent-skill-{index}.pdf",
            }
            if index == 9:
                paper = {
                    "title": "DOI Only Agent Skill",
                    "source": "crossref",
                    "paper_id": "10.2139/example",
                    "doi": "10.2139/example",
                    "url": "https://doi.org/10.2139/example",
                }
            if index == 10:
                paper = {
                    "title": "Publisher Landing Page Agent Skill",
                    "source": "openalex",
                    "paper_id": "W123456789",
                    "doi": "10.1016/j.example.2026.01.001",
                    "url": "https://doi.org/10.1016/j.example.2026.01.001",
                }
            papers.append(paper)

        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["arxiv", "crossref"],
            "source_results": {"arxiv": 59, "crossref": 1},
            "errors": {},
            "papers": papers,
            "total": len(papers),
            "raw_total": len(papers),
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "app_only",
            },
        ), patch(
            "paper_search_mcp.engine.parse._codex_app_display_enabled",
            return_value=True,
        ):
            from paper_search_mcp.tools import orchestration

            result = asyncio.run(
                orchestration._create_paper_selection_result(
                    query="agent skill",
                    max_results_per_source=60,
                    sources="arxiv,crossref",
                    year=None,
                    search_result=fake_search_result,
                    interaction="crawl_papers_for_selection",
                    action_tool="download_selected_papers",
                    action_verb="download",
                    selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                    requested_count=11,
                )
            )
            loaded = cache.get_search_session(result["selection_token"], cache_dir=tmp)
            full_loaded = cache.get_search_session(result["full_selection_token"], cache_dir=tmp)
            rendered = asyncio.run(
                server.render_paper_selection_app(
                    selection_token=result["selection_token"],
                )
            )
            rendered_payload = unwrap_tool_result(rendered)

        app_indices = [paper["index"] for paper in result["app"]["papers"]]
        source_indices = [paper["source_index"] for paper in result["app"]["papers"]]
        self.assertEqual(len(result["papers"]), 11)
        self.assertEqual(len(loaded["papers"]), 11)
        self.assertEqual(len(full_loaded["papers"]), 60)
        self.assertEqual(result["full_selection_token"], loaded["metadata"]["full_selection_token"])
        self.assertEqual(
            loaded["metadata"]["selection_session_role"],
            "display_shortlist",
        )
        self.assertEqual(len(result["app"]["papers"]), 11)
        self.assertEqual(app_indices, list(range(1, 12)))
        self.assertEqual(source_indices, [1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13])
        self.assertEqual(result["source_indices"], source_indices)
        self.assertEqual(result["recommended_selected_indices"], "1-11")
        self.assertEqual(result["app"]["full_total"], 60)
        self.assertEqual(result["app"]["requested_count"], 11)
        self.assertEqual(len(rendered_payload["papers"]), 11)
        self.assertEqual(
            [paper["index"] for paper in rendered_payload["papers"]],
            app_indices,
        )
        self.assertEqual(
            [paper["source_index"] for paper in rendered_payload["papers"]],
            source_indices,
        )
        self.assertEqual(rendered_payload["full_total"], 60)
        self.assertEqual(rendered_payload["requested_count"], 11)
        self.assertNotIn("local_browser", result)

    def test_selection_result_uses_request_sized_display_payload_for_all_hosts(self):
        papers = [
            {
                "title": f"Claude Candidate {index}",
                "source": "arxiv",
                "paper_id": f"2606.{index:05d}",
                "pdf_url": f"https://example.org/claude-{index}.pdf",
            }
            for index in range(1, 15)
        ]
        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": len(papers)},
            "errors": {},
            "papers": papers,
            "total": len(papers),
            "raw_total": len(papers),
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
            },
        ):
            from paper_search_mcp.tools import orchestration

            result = asyncio.run(
                orchestration._create_paper_selection_result(
                    query="agent skill",
                    max_results_per_source=14,
                    sources="arxiv",
                    year=None,
                    search_result=fake_search_result,
                    interaction="crawl_papers_for_selection",
                    action_tool="download_selected_papers",
                    action_verb="download",
                    selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                    requested_count=11,
                )
            )

        self.assertEqual(result["recommended_selected_indices"], "1-11")
        self.assertEqual(len(result["papers"]), 11)
        self.assertEqual(result["full_total"], 14)
        self.assertEqual(len(result["app"]["papers"]), 11)
        self.assertEqual(result["app"]["full_total"], 14)

    def test_crawl_papers_for_selection_includes_numbered_fallback_without_ui(self):
        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["semantic"],
            "source_results": {"semantic": 2},
            "errors": {},
            "papers": [
                {"title": "Agent Skill Libraries", "source": "semantic", "paper_id": "s1"},
                {"title": "Skill Retrieval for LLM Agents", "source": "semantic", "paper_id": "s2"},
            ],
            "total": 2,
            "raw_total": 2,
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ), patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=fake_search_result),
        ):
            result = asyncio.run(server.crawl_papers_for_selection("agent skill", sources="semantic"))

        self.assertEqual(result["fallback"]["interaction"], "backend_session_numbered_selection")
        self.assertEqual(result["fallback"]["selection_token"], result["selection_token"])
        self.assertEqual(result["numbered_fallback"][0].split(".", 1)[0], "1")
        self.assertIn("Agent Skill Libraries", result["numbered_fallback"][0])

    def test_crawl_agent_skill_defaults_to_fast_sources(self):
        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": 0},
            "errors": {},
            "papers": [],
            "total": 0,
            "raw_total": 0,
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ), patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=fake_search_result),
        ) as search_mock:
            asyncio.run(
                server.crawl_papers_for_selection(
                    "agent skill",
                    ranking_profile="agent-skill",
                )
            )

        self.assertEqual(search_mock.await_args.kwargs["sources"], "agent-skill-fast")

    def test_parse_selected_papers_uses_session_and_direct_pdf_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "downloaded.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="mineru parsing",
                sources="arxiv",
                papers=[
                    {
                        "title": "MinerU Parsing Paper",
                        "authors": "A. Researcher",
                        "source": "arxiv",
                        "paper_id": "2601.00002",
                        "doi": "10.1000/mineru",
                        "pdf_url": "https://example.org/mineru.pdf",
                        "url": "https://example.org/mineru",
                    }
                ],
                cache_dir=tmp,
            )

            parse_payload = {
                "status": "ok",
                "full_md_path": str(Path(tmp) / "full.md"),
                "result_zip_path": str(Path(tmp) / "downloaded.zip"),
            }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.parse_pdf_with_mineru",
                new=AsyncMock(return_value=parse_payload),
            ):
                result = asyncio.run(
                    server.parse_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        mode="pypdf",
                    )
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parsed"], 1)
            self.assertEqual(result["results"][0]["download_method"], "search_result_pdf_url")
            self.assertEqual(result["results"][0]["parse"]["result_zip_path"], parse_payload["result_zip_path"])

    def test_parse_candidate_normalizes_arxiv_url_and_canonical_filename(self):
        candidate = server._paper_parse_candidate(
            {
                "title": "SkillCraft",
                "source": "google_scholar",
                "paper_id": "gs_123",
                "url": "https://arxiv.org/abs/2603.00718",
            },
            1,
        )

        self.assertEqual(candidate["source"], "arxiv")
        self.assertEqual(candidate["paper_id"], "2603.00718")
        self.assertEqual(candidate["pdf_url"], "https://arxiv.org/pdf/2603.00718")
        self.assertEqual(candidate["canonical_pdf_stem"], "2603.00718")

    def test_download_selected_papers_default_does_not_submit_parse_job_under_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "download-only.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="download only",
                sources="arxiv",
                papers=[
                    {
                        "title": "Download Only Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00009",
                        "pdf_url": "https://example.org/download-only.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("default download must not submit a parse job")),
            ) as submit_mock:
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )
                manifest_exists = Path(result["manifest_path"]).exists()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(result["results"][0]["status"], "downloaded")
        self.assertNotIn("parse", result["results"][0])
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")
        self.assertNotIn("parse_job", result["parse_prompt"])
        self.assertIn("not started", result["parse_prompt"]["message"])
        self.assertFalse(result["parse_prompt"]["parse_decision_required"])
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["parse_ready_total"], 1)
        self.assertTrue(manifest_exists)
        submit_mock.assert_not_awaited()

    def test_download_selected_papers_background_submits_parse_job_under_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "download-and-parse.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="download and parse",
                sources="arxiv",
                papers=[
                    {
                        "title": "Download And Parse Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00019",
                        "pdf_url": "https://example.org/download-and-parse.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.tools.core._run_submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-test"}),
            ) as submit_mock:
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="background",
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(result["parse_execution"], "background")
        self.assertEqual(result["parse_prompt"]["interaction"], "auto_parse_saved_pdfs")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "get_parse_job_status")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "1")
        self.assertEqual(result["parse_prompt"]["parse_job"]["job_id"], "parse-test")
        submit_mock.assert_awaited_once()
        self.assertEqual(submit_mock.await_args.kwargs["selected_indices"], "1")

    def test_download_selected_papers_skip_parse_execution_disables_auto_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "download-only.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="download only",
                sources="arxiv",
                papers=[
                    {
                        "title": "Download Only Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00009",
                        "pdf_url": "https://example.org/download-only.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("skip must not submit a parse job")),
            ) as submit_mock:
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="skip",
                    )
                )
                manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
                pending_state = cache.read_parse_prompt_state(
                    session["selection_token"], cache_dir=tmp
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")
        self.assertNotIn("parse_job", result["parse_prompt"])
        self.assertIn("not started", result["parse_prompt"]["message"])
        self.assertFalse(result["parse_prompt"]["parse_decision_required"])
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["default_parse_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["timeout_seconds"], 180)
        self.assertEqual(result["parse_prompt"]["download_selection_token"], session["selection_token"])
        self.assertEqual(pending_state, {})
        self.assertEqual(manifest["parse_execution"], "none")
        submit_mock.assert_not_awaited()

    def test_parse_prompt_timeout_is_terminal_and_suppresses_reprompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "timeout.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="timeout prompt",
                sources="arxiv",
                papers=[
                    {
                        "title": "Timeout Prompt Paper",
                        "source": "arxiv",
                        "paper_id": "2606.09991v1",
                        "pdf_url": "https://example.org/timeout.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("timeout must not submit parse jobs")),
            ):
                first = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )
                prompt = first["parse_prompt"]
                terminal = dismiss_parse_prompt_state(
                    session["selection_token"],
                    prompt_id=prompt["prompt_id"],
                    reason="timeout",
                )
                repeated = dismiss_parse_prompt_state(
                    session["selection_token"],
                    prompt_id=prompt["prompt_id"],
                    reason="timeout",
                )
                second = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )

        self.assertEqual(terminal["status"], "timed_out_no_parse")
        self.assertTrue(terminal["terminal"])
        self.assertFalse(terminal["parse_decision_required"])
        self.assertEqual(terminal["recommended_tool"], "")
        self.assertIn("180 seconds", terminal["message"])
        self.assertEqual(repeated["status"], "timed_out_no_parse")
        self.assertEqual(repeated["prompt_id"], terminal["prompt_id"])
        self.assertEqual(second["parse_prompt"]["status"], "timed_out_no_parse")
        self.assertFalse(second["parse_prompt"]["parse_decision_required"])
        self.assertNotEqual(second.get("interaction"), "mcp_app")

    def test_download_selected_papers_skip_parse_execution_ignores_recent_over_limit_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                recent = Path(tmp) / f"recent-skip-{index + 1}.pdf"
                recent.write_bytes(b"%PDF-1.4\n%%EOF")

            pdf = Path(tmp) / "download-only-new.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="download only recent",
                sources="arxiv",
                papers=[
                    {
                        "title": "Download Only Recent Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00019",
                        "pdf_url": "https://example.org/download-only-new.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_PROMPT": "true",
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_WINDOW_SECONDS": "3600",
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/parse-after-download"}),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("skip must not submit parse jobs")),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="skip",
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")
        self.assertFalse(result["parse_prompt"]["parse_decision_required"])
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")

    def test_download_selected_papers_prompt_parse_execution_requires_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "prompt-only.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="prompt only",
                sources="arxiv",
                papers=[
                    {
                        "title": "Prompt Only Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00010",
                        "pdf_url": "https://example.org/prompt-only.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value=str(pdf)),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("prompt mode should not auto-submit")),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="prompt",
                    )
                )

        self.assertEqual(result["parse_prompt"]["parse_execution"], "prompt")
        self.assertTrue(result["parse_prompt"]["parse_decision_required"])
        self.assertEqual(result["parse_prompt"]["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "")

    def test_download_selected_papers_recent_directory_over_limit_returns_checkbox_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT):
                pdf = Path(tmp) / f"recent-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            papers = []
            for index in range(2):
                papers.append(
                    {
                        "title": f"New Batch Paper {index + 1}",
                        "source": "arxiv",
                        "paper_id": f"2601.99{index}",
                        "pdf_url": f"https://example.org/new-{index + 1}.pdf",
                    }
                )
            session = cache.create_search_session(
                query="split batch",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            async def fake_download_wrapper(**kwargs):
                pdf = Path(kwargs["save_path"]) / f"new-{kwargs['index']}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return {
                    "index": kwargs["index"],
                    "status": "downloaded",
                    "candidate": {"title": f"New Batch Paper {kwargs['index']}"},
                    "download_method": "test",
                    "pdf_path": str(pdf),
                    "valid_pdf": True,
                }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_PROMPT": "true",
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_WINDOW_SECONDS": "3600",
                    "PAPER_SEARCH_MCP_AUTO_OPEN_SELECTION_UI": "true",
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._download_selected_session_paper_wrapper",
                new=AsyncMock(side_effect=fake_download_wrapper),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("recent large batches should ask before parsing")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/recent"}),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        prompt = result["parse_prompt"]
        self.assertEqual(result["downloaded"], 2)
        self.assertEqual(prompt["parse_execution"], "none")
        self.assertEqual(prompt["recommended_tool"], "submit_parse_job")
        self.assertEqual(prompt["recommended_selected_indices"], "all")
        self.assertEqual(prompt["total"], 2)
        self.assertFalse(prompt["parse_decision_required"])
        self.assertNotIn("parse_job", prompt)
        self.assertNotIn("local_browser", prompt)

    def test_download_selected_papers_defaults_to_desktop_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="default save path",
                sources="arxiv",
                papers=[
                    {
                        "title": "Default Path Paper",
                        "source": "arxiv",
                        "paper_id": "2601.12345v1",
                        "pdf_url": "https://example.org/default.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "USERPROFILE": tmp,
                },
                clear=True,
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("default save path flow must not auto-parse")),
            ) as submit_mock:
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                    )
                )

        expected_root = Path(tmp) / "Desktop" / "papers"
        self.assertEqual(Path(result["save_path"]), expected_root.resolve())
        self.assertTrue(result["save_path_defaulted"])
        self.assertEqual(Path(result["default_save_path"]), expected_root.resolve())
        self.assertEqual(Path(result["results"][0]["pdf_path"]).parent, expected_root.resolve())
        self.assertEqual(Path(result["results"][0]["pdf_path"]).name, "2601.12345v1.pdf")
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertNotIn("parse_job", result["parse_prompt"])
        submit_mock.assert_not_awaited()

    def test_download_selected_papers_rejects_unconfirmed_custom_save_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="unconfirmed custom path",
                sources="arxiv",
                papers=[
                    {
                        "title": "Unconfirmed Path Paper",
                        "source": "arxiv",
                        "paper_id": "2601.54321v1",
                        "pdf_url": "https://example.org/custom.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "USERPROFILE": tmp,
                },
                clear=True,
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("unconfirmed custom path must not download")),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                    )
                )

        self.assertEqual(result["status"], "invalid_save_path")
        self.assertEqual(result["confirm_param"], "custom_save_path_confirmed")
        self.assertEqual(Path(result["default_save_path"]), (Path(tmp) / "Desktop" / "papers").resolve())

    def test_download_selected_papers_over_limit_returns_checkbox_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                papers.append(
                    {
                        "title": f"Batch Paper {index + 1}",
                        "source": "arxiv",
                        "paper_id": f"2601.{index + 1:05d}",
                        "pdf_url": f"https://example.org/{index + 1}.pdf",
                    }
                )
            session = cache.create_search_session(
                query="download many",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._download_from_url",
                new=AsyncMock(side_effect=AssertionError("large parse batches must ask before downloading")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("large batches should ask before parsing")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/batch"}),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "open_paper_selection_page")
        self.assertEqual(result["recommended_tool"], "open_paper_selection_page")
        self.assertEqual(result["interaction"], "local_browser_checkbox")
        self.assertRegex(result["local_browser_url"], r"^http://127\.0\.0\.1:\d+/paper-selection/")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "")
        self.assertEqual(result["parse_prompt"]["total"], server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
        self.assertEqual(result["parse_prompt"]["selection_semantics"], "download_selected_only")
        self.assertEqual(result["app"]["render_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertNotIn("_meta", result)
        self.assertTrue(result["parse_prompt"]["parse_decision_required"])
        self.assertEqual(result["parse_prompt"]["local_browser"]["interaction"], "local_browser_checkbox")
        self.assertEqual(result["selection_timeout_seconds"], 180)

    def test_download_selection_timeout_defaults_to_180_and_scales_with_count(self):
        with patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS": "15"},
            clear=True,
        ):
            self.assertEqual(server._download_selection_timeout_seconds(1), 180)
            self.assertEqual(server._download_selection_timeout_seconds(11), 180)
            self.assertEqual(server._download_selection_timeout_seconds(20), 300)

        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_DOWNLOAD_SELECTION_TIMEOUT_SECONDS": "240",
                "PAPER_SEARCH_MCP_PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS": "30",
            },
            clear=True,
        ):
            self.assertEqual(server._download_selection_timeout_seconds(5), 240)
            self.assertEqual(server._download_selection_timeout_seconds(12), 360)

    def test_paper_research_workflow_over_limit_prefers_local_browser_in_vscode(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1},
                "errors": {},
                "papers": [
                    {
                        "title": f"Agent Skill {index}",
                        "source": "arxiv",
                        "paper_id": f"2601.{index:05d}",
                        "pdf_url": f"https://example.org/{index}.pdf",
                    }
                    for index in range(1, server.AUTO_PARSE_SAVED_PDF_LIMIT + 2)
                ],
                "total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                "raw_total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
            }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode",
                    "PAPER_SEARCH_MCP_PARSE_PROMPT_TIMEOUT_PER_PAPER_SECONDS": "15",
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ):
                server.detect_host.cache_clear()
                result = asyncio.run(
                    server.paper_research_workflow(
                        "agent skill",
                        count=server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )
                server.detect_host.cache_clear()

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["interaction"], "local_browser_checkbox")
        self.assertEqual(result["recommended_tool"], "open_paper_selection_page")
        self.assertRegex(result["local_browser_url"], r"^http://127\.0\.0\.1:\d+/paper-selection/")
        self.assertNotIn("_meta", result)
        self.assertEqual(result["selection_timeout_seconds"], 180)

    def test_download_selected_papers_over_limit_repeats_until_explicit_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Repeat Prompt Paper {index + 1}",
                    "source": "arxiv",
                    "paper_id": f"2608.{index + 1:05d}",
                    "pdf_url": f"https://example.org/repeat-{index + 1}.pdf",
                }
                for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            ]
            session = cache.create_search_session(
                query="repeat prompt",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("large batches must ask before downloading")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/repeat"}),
            ):
                first = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )
                loaded = cache.get_search_session(session["selection_token"], cache_dir=tmp)
                second = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(first["status"], "selection_required")
        self.assertFalse(loaded["metadata"].get("large_batch_selection_satisfied", False))
        self.assertEqual(second["status"], "selection_required")
        self.assertNotEqual(first["selection_token"], session["selection_token"])
        self.assertNotEqual(second["selection_token"], session["selection_token"])

    def test_download_selected_papers_over_limit_download_only_requires_checkbox_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Download Only Batch {index + 1}",
                    "source": "arxiv",
                    "paper_id": f"2607.{index + 1:05d}",
                    "pdf_url": f"https://example.org/download-only-{index + 1}.pdf",
                }
                for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            ]
            session = cache.create_search_session(
                query="download only many",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("large download-only batches must ask before downloading")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("download-only must not submit parse jobs")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/download-only"}),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertEqual(result["app"]["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertEqual(result["parse_prompt"]["local_browser"]["interaction"], "local_browser_checkbox")

    def test_download_selected_papers_over_limit_public_bypass_still_requires_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Download Only Bypass Batch {index + 1}",
                    "source": "arxiv",
                    "paper_id": f"2607.{index + 1:05d}",
                    "pdf_url": f"https://example.org/download-only-bypass-{index + 1}.pdf",
                }
                for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            ]
            session = cache.create_search_session(
                query="download only many bypass",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("public bypass must not download over-limit batches")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("download-only must not submit parse jobs")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/public-bypass"}),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                        bypass_large_batch_selection=True,
                    )
                )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(result["parse_prompt"]["local_browser"]["interaction"], "local_browser_checkbox")
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")

    def test_download_selected_papers_over_limit_never_policy_still_requires_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Never Policy Batch {index + 1}",
                    "source": "arxiv",
                    "paper_id": f"2609.{index + 1:05d}",
                    "pdf_url": f"https://example.org/never-policy-{index + 1}.pdf",
                }
                for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            ]
            session = cache.create_search_session(
                query="never policy many",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("large public never policy must not download")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/never-policy"}),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                        large_batch_selection="never",
                        bypass_large_batch_selection=True,
                    )
                )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(result["parse_execution"], "none")

    def test_download_selected_papers_at_limit_waits_for_parse_decision_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT):
                papers.append(
                    {
                        "title": f"Limit Paper {index + 1}",
                        "source": "arxiv",
                        "paper_id": f"2602.{index + 1:05d}",
                        "pdf_url": f"https://example.org/limit-{index + 1}.pdf",
                    }
                )
            session = cache.create_search_session(
                query="download at limit",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("default download must not submit parse jobs")),
            ) as submit_mock:
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        expected_indices = ",".join(str(index) for index in range(1, server.AUTO_PARSE_SAVED_PDF_LIMIT + 1))
        self.assertEqual(result["downloaded"], server.AUTO_PARSE_SAVED_PDF_LIMIT)
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["default_parse_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["parse_ready_total"], server.AUTO_PARSE_SAVED_PDF_LIMIT)
        self.assertNotIn("parse_job", result["parse_prompt"])
        self.assertFalse(result["parse_prompt"]["parse_decision_required"])
        submit_mock.assert_not_awaited()

    def test_download_selected_papers_skips_existing_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "existing.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="existing pdf",
                sources="local",
                papers=[
                    {
                        "title": "Existing PDF",
                        "source": "local",
                        "paper_id": "existing",
                        "local_pdf_path": str(pdf),
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("existing PDF should be skipped")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("default existing PDF flow must not submit parse jobs")),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["results"][0]["status"], "skipped_existing")
        self.assertTrue(result["results"][0]["valid_pdf"])
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertNotIn("parse_job", result["parse_prompt"])

    def test_crawl_download_parse_papers_default_downloads_without_parse_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": 2},
                "errors": {},
                "papers": [
                    {
                        "title": "Agent Skill One",
                        "source": "arxiv",
                        "paper_id": "2601.10001v1",
                        "pdf_url": "https://example.org/one.pdf",
                    },
                    {
                        "title": "Agent Skill Two",
                        "source": "arxiv",
                        "paper_id": "2601.10002v1",
                        "pdf_url": "https://example.org/two.pdf",
                    },
                ],
                "total": 2,
                "raw_total": 2,
            }

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ) as search_mock, patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("default crawl download must not submit parse jobs")),
            ) as submit_mock:
                result = asyncio.run(
                    server.crawl_download_parse_papers(
                        "agent skill",
                        count=2,
                        ranking_profile="agent-skill",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )
                downloaded_names = sorted(path.name for path in Path(tmp).glob("*.pdf"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["download"]["downloaded"], 2)
        self.assertEqual(result["download"]["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertNotIn("parse_job", result["parse_prompt"])
        self.assertEqual(downloaded_names[0], "2601.10001v1.pdf")
        search_mock.assert_called_once()
        self.assertIn("arxiv", search_mock.call_args.args[0])
        self.assertIn("crossref", search_mock.call_args.args[0])
        submit_mock.assert_not_awaited()

    def test_crawl_download_parse_papers_background_submits_parse_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": 1},
                "errors": {},
                "papers": [
                    {
                        "title": "Agent Skill One",
                        "source": "arxiv",
                        "paper_id": "2601.10011v1",
                        "pdf_url": "https://example.org/one.pdf",
                    }
                ],
                "total": 1,
                "raw_total": 1,
            }

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.tools.orchestration._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.tools.core._run_submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-crawl"}),
            ) as submit_mock:
                result = asyncio.run(
                    server.crawl_download_parse_papers(
                        "agent skill",
                        count=1,
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="background",
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["download"]["parse_execution"], "background")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "get_parse_job_status")
        self.assertEqual(result["parse_prompt"]["parse_job"]["job_id"], "parse-crawl")
        submit_mock.assert_awaited_once()

    def test_paper_research_workflow_default_downloads_without_parse_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": 1},
                "errors": {},
                "papers": [
                    {
                        "title": "Agent Skill One",
                        "source": "arxiv",
                        "paper_id": "2601.10001v1",
                        "pdf_url": "https://example.org/one.pdf",
                    }
                ],
                "total": 1,
                "raw_total": 1,
            }

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("default workflow must not submit parse jobs")),
            ) as submit_mock:
                result = asyncio.run(
                    server.paper_research_workflow(
                        "agent skill",
                        count=1,
                        ranking_profile="agent-skill",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["workflow"]["mcp_first"])
        self.assertEqual(result["workflow"]["next_tool"], "")
        self.assertEqual(result["workflow"]["parse_execution"], "none")
        self.assertEqual(result["download"]["downloaded"], 1)
        self.assertEqual(result["download"]["parse_execution"], "none")
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertNotIn("parse_job", result["parse_prompt"])
        submit_mock.assert_not_awaited()

    def test_paper_research_workflow_explicit_parse_submits_parse_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": 1},
                "errors": {},
                "papers": [
                    {
                        "title": "Agent Skill One",
                        "source": "arxiv",
                        "paper_id": "2601.10021v1",
                        "pdf_url": "https://example.org/one.pdf",
                    }
                ],
                "total": 1,
                "raw_total": 1,
            }

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.tools.orchestration._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.tools.core._run_submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-test"}),
            ) as submit_mock:
                result = asyncio.run(
                    server.paper_research_workflow(
                        "agent skill",
                        intent="download_and_parse",
                        count=1,
                        ranking_profile="agent-skill",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="background",
                    )
                )

        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["workflow"]["mcp_first"])
        self.assertEqual(result["workflow"]["next_tool"], "get_parse_job_status")
        self.assertEqual(result["workflow"]["parse_execution"], "background")
        self.assertEqual(result["parse_job"]["job_id"], "parse-test")
        submit_mock.assert_awaited_once()
        self.assertEqual(submit_mock.await_args.kwargs["selected_indices"], "1")

    def test_paper_research_workflow_download_only_over_limit_still_requires_parse_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1},
                "errors": {},
                "papers": [
                    {
                        "title": f"Agent Skill {index + 1}",
                        "source": "arxiv",
                        "paper_id": f"2601.{index + 1:05d}",
                        "pdf_url": f"https://example.org/{index + 1}.pdf",
                    }
                    for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
                ],
                "total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                "raw_total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
            }

            async def fake_download(_url, save_path, filename_hint="paper"):
                pdf = Path(save_path) / f"{filename_hint}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return str(pdf)

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("large workflow parse batches must ask before downloading")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("large workflow downloads must ask before parsing")),
            ):
                result = asyncio.run(
                    server.paper_research_workflow(
                        "agent skill",
                        intent="search_download",
                        count=server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["workflow"]["next_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(result["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertNotIn("download", result)
        self.assertNotIn("parse_prompt", result)
        self.assertEqual(result["parse_execution"], "none")
        self.assertEqual(result["selection_semantics"], "download_selected_only")
        self.assertEqual(result["app"]["selection_semantics"], "download_selected_only")

    def test_crawl_download_parse_papers_over_limit_requires_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1},
                "errors": {},
                "papers": [
                    {
                        "title": f"Compat Agent Skill {index + 1}",
                        "source": "arxiv",
                        "paper_id": f"2608.{index + 1:05d}",
                        "pdf_url": f"https://example.org/compat-{index + 1}.pdf",
                    }
                    for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
                ],
                "total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                "raw_total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
            }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("compat workflow must ask before large downloads")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("compat workflow must ask before parsing")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/compat"}),
            ):
                result = asyncio.run(
                    server.crawl_download_parse_papers(
                        "agent skill",
                        count=server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
        )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["parse_prompt"]["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertEqual(result["parse_prompt"]["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertNotIn("results", result)

    def test_paper_research_workflow_parse_none_over_limit_requires_download_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_search_result = {
                "query": "agent skill",
                "sources_used": ["arxiv"],
                "source_results": {"arxiv": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1},
                "errors": {},
                "papers": [
                    {
                        "title": f"Agent Skill Download {index + 1}",
                        "source": "arxiv",
                        "paper_id": f"2602.{index + 1:05d}",
                        "pdf_url": f"https://example.org/download-{index + 1}.pdf",
                    }
                    for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
                ],
                "total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                "raw_total": server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
            }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._cached_search_result",
                return_value=fake_search_result,
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=AssertionError("large download-only workflow must ask before downloading")),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("parse_execution none must not submit parse jobs")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/workflow-download"}),
            ):
                result = asyncio.run(
                    server.paper_research_workflow(
                        "agent skill",
                        intent="search_download",
                        count=server.AUTO_PARSE_SAVED_PDF_LIMIT + 1,
                        parse_execution="none",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["workflow"]["next_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(result["parse_execution"], "none")
        self.assertNotIn("parse_prompt", result)
        self.assertEqual(result["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertEqual(result["app"]["selection_semantics"], server.SELECTION_SEMANTICS_DOWNLOAD_ONLY)
        self.assertNotIn("download", result)

    def test_paper_research_workflow_search_only_returns_selection_without_download(self):
        fake_search_result = {
            "query": "agent skill",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": 1},
            "errors": {},
            "papers": [
                {
                    "title": "Agent Skill One",
                    "source": "arxiv",
                    "paper_id": "2601.10001v1",
                    "pdf_url": "https://example.org/one.pdf",
                }
            ],
            "total": 1,
            "raw_total": 1,
        }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ), patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=fake_search_result),
        ), patch(
            "paper_search_mcp.server.download_selected_papers",
            new=AsyncMock(side_effect=AssertionError("search-only workflow must not download")),
        ):
            result = asyncio.run(
                server.paper_research_workflow(
                    "agent skill",
                    intent="search_only",
                    selection_mode="manual",
                )
            )

        self.assertEqual(result["status"], "selection_ready")
        self.assertTrue(result["workflow"]["mcp_first"])
        self.assertEqual(result["workflow"]["next_tool"], "render_paper_selection_app")
        self.assertIn("selection_token", result["selection"])

    def test_agent_skill_ranking_profile_orders_relevant_papers_first(self):
        papers = [
            {
                "title": "Piano Skill Acquisition in Adult Learners",
                "abstract": "A human skill learning study for piano practice.",
                "source": "semantic",
                "paper_id": "human",
                "score": 5,
            },
            {
                "title": "Agent Skill Libraries for LLM Agents",
                "abstract": "We study skill retrieval, skill revision, and skill security for tool-using agents.",
                "source": "arxiv",
                "paper_id": "agent",
                "score": 1,
            },
        ]

        ranked = server._rank_papers_for_profile(
            papers,
            ranking_profile="agent-skill",
            query="agent skill",
        )

        self.assertEqual(ranked[0]["paper_id"], "agent")
        self.assertGreater(ranked[0]["profile_score"], ranked[1]["profile_score"])
        self.assertEqual(ranked[0]["ranking_profile"], "agent-skill")

    def test_parse_selected_papers_respects_parse_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Local Paper {index}",
                    "source": "local",
                    "paper_id": str(index),
                    "local_pdf_path": str(Path(tmp) / f"paper-{index}.pdf"),
                }
                for index in range(3)
            ]
            for paper in papers:
                Path(paper["local_pdf_path"]).write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="parallel parse",
                sources="local",
                papers=papers,
                cache_dir=tmp,
            )
            active = 0
            peak = 0

            async def fake_parse(**kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1
                return {"status": "ok", "full_md_path": kwargs["pdf_path"] + ".md"}

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_PARSE_CONCURRENCY": "2",
                },
            ), patch(
                "paper_search_mcp.server.parse_pdf_with_mineru",
                new=AsyncMock(side_effect=fake_parse),
            ):
                result = asyncio.run(
                    server.parse_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["parse_concurrency"], 2)
            self.assertEqual(result["parsed"], 3)
            self.assertEqual(peak, 2)

    def test_parse_selected_papers_uses_mineru_batch_for_extract_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(2):
                pdf = Path(tmp) / f"batch-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                papers.append(
                    {
                        "title": f"Batch Paper {index + 1}",
                        "source": "local",
                        "paper_id": str(index + 1),
                        "local_pdf_path": str(pdf),
                    }
                )
            session = cache.create_search_session(
                query="batch parse",
                sources="local",
                papers=papers,
                cache_dir=tmp,
            )

            batch_payload = [
                {"status": "ok", "paper_key": "batch-1", "full_md_path": str(Path(tmp) / "one.md")},
                {"status": "ok", "paper_key": "batch-2", "full_md_path": str(Path(tmp) / "two.md")},
            ]

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_MINERU_API_KEY": "test-token",
                },
            ), patch(
                "paper_search_mcp.server.run_parse_pdfs_with_mineru",
                return_value=batch_payload,
            ) as batch_mock, patch(
                "paper_search_mcp.server.parse_pdf_with_mineru",
                new=AsyncMock(side_effect=AssertionError("single parse should not be used")),
            ):
                result = asyncio.run(
                    server.parse_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        mode="extract",
                    )
                )

            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["batch_parse"]["attempted"])
            self.assertEqual(result["parsed"], 2)
            batch_mock.assert_called_once()
            self.assertEqual(len(batch_mock.call_args.args[0]), 2)

    def test_submit_parse_job_runs_in_background(self):
        async def fake_parse_selected_papers(**kwargs):
            await asyncio.sleep(0)
            return {"status": "ok", "parsed": 1, "total": 1, "selection_token": kwargs["selection_token"]}

        async def run_case():
            with patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(side_effect=fake_parse_selected_papers),
            ):
                submitted = await server.submit_parse_job("job-token", selected_indices="1")
                for _ in range(10):
                    status = await server.get_parse_job_status(submitted["job_id"])
                    if status["status"] == "completed":
                        return submitted, status
                    await asyncio.sleep(0.01)
                return submitted, await server.get_parse_job_status(submitted["job_id"])

        submitted, status = asyncio.run(run_case())

        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["result"]["parsed"], 1)

    def test_submit_parse_job_exposes_progress_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": "Progress Paper",
                    "source": "arxiv",
                    "paper_id": "2606.00001",
                    "local_pdf_path": str(Path(tmp) / "progress.pdf"),
                }
            ]
            Path(papers[0]["local_pdf_path"]).write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session("progress", "arxiv", papers, cache_dir=tmp)

            async def fake_parse_selected_papers(**kwargs):
                job_id = server._CURRENT_PARSE_JOB_ID.get("")
                server._update_parse_job_item(job_id, 1, status="parsing", message="MinerU parsing is running.")
                await asyncio.sleep(0)
                server._update_parse_job_item(job_id, 1, status="ok", message="MinerU parse ok.")
                return {"status": "ok", "parsed": 1, "failed": 0, "skipped": 0, "total": 1}

            async def run_case():
                with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                    "paper_search_mcp.server.parse_selected_papers",
                    new=AsyncMock(side_effect=fake_parse_selected_papers),
                ):
                    submitted = await server.submit_parse_job(session["selection_token"], selected_indices="1")
                    first = await server.get_parse_job_status(submitted["job_id"])
                    for _ in range(20):
                        status = await server.get_parse_job_status(submitted["job_id"])
                        if status["status"] == "completed":
                            return submitted, first, status
                        await asyncio.sleep(0.01)
                    return submitted, first, await server.get_parse_job_status(submitted["job_id"])

            submitted, first, status = asyncio.run(run_case())

        self.assertEqual(submitted["total"], 1)
        self.assertEqual(submitted["items"][0]["title"], "Progress Paper")
        self.assertEqual(first["items"][0]["index"], 1)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["items"][0]["status"], "ok")
        self.assertEqual(status["progress_percent"], 100)
        self.assertEqual(status["completed_items"], 1)

    def test_submit_parse_job_persists_completed_status(self):
        async def fake_parse_selected_papers(**kwargs):
            await asyncio.sleep(0)
            return {"status": "ok", "parsed": 1, "total": 1}

        async def run_case(tmp):
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(side_effect=fake_parse_selected_papers),
            ):
                submitted = await server.submit_parse_job("persist-token", selected_indices="1")
                for _ in range(10):
                    status = await server.get_parse_job_status(submitted["job_id"])
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)
                with server._PARSE_JOB_LOCK:
                    server._PARSE_JOBS.clear()
                persisted = await server.get_parse_job_status(submitted["job_id"])
                listed = await server.list_parse_jobs()
                return persisted, listed

        with tempfile.TemporaryDirectory() as tmp:
            persisted, listed = asyncio.run(run_case(tmp))

        self.assertEqual(persisted["status"], "completed")
        self.assertFalse(persisted["active"])
        self.assertEqual(listed["jobs"][0]["status"], "completed")

    def test_submit_parse_job_survives_closed_calling_loop(self):
        async def fake_parse_selected_papers(**kwargs):
            await asyncio.sleep(0.02)
            return {"status": "ok", "parsed": 1, "total": 1, "selection_token": kwargs["selection_token"]}

        with patch(
            "paper_search_mcp.server.parse_selected_papers",
            new=AsyncMock(side_effect=fake_parse_selected_papers),
        ):
            submitted = asyncio.run(server.submit_parse_job("closed-loop-token", selected_indices="1"))

            status = {}
            for _ in range(30):
                status = asyncio.run(server.get_parse_job_status(submitted["job_id"]))
                if status["status"] == "completed":
                    break
                import time

                time.sleep(0.02)

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["result"]["selection_token"], "closed-loop-token")

    def test_parse_selected_papers_rejects_custom_save_path_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="mineru parsing",
                sources="arxiv",
                papers=[{"title": "Paper", "source": "arxiv", "paper_id": "2601.00002"}],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "false",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ):
                result = asyncio.run(
                    server.parse_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                    )
                )

            self.assertEqual(result["status"], "invalid_save_path")
            self.assertEqual(result["allow_env"], "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH")

    def test_after_saved_pdf_auto_submits_parse_job_without_context_when_under_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "saved.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-saved"}),
            ) as submit_mock:
                result = asyncio.run(
                    server._after_saved_pdf(
                        str(pdf),
                        source="arxiv",
                        paper_id="2601.00005",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                        custom_save_path_confirmed=True,
                    )
                )

            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(result["pdf_path"], str(pdf.resolve()))
            prompt = result["parse_prompt"]
            self.assertEqual(prompt["status"], "submitted")
            self.assertEqual(prompt["interaction"], "auto_parse_saved_pdfs")
            self.assertEqual(prompt["selected_indices"], [1])
            self.assertEqual(prompt["recommended_tool"], "get_parse_job_status")
            self.assertEqual(prompt["recommended_selected_indices"], "1")
            self.assertEqual(prompt["parse_job"]["job_id"], "parse-saved")
            self.assertEqual(prompt["auto_parse_limit"], server.AUTO_PARSE_SAVED_PDF_LIMIT)
            self.assertNotIn("app", result)
            submit_mock.assert_awaited_once()
            self.assertEqual(submit_mock.await_args.kwargs["selected_indices"], "1")

            loaded = cache.get_search_session(prompt["selection_token"], cache_dir=tmp)
            self.assertEqual(loaded["metadata"]["trigger"], "pdf_saved")
            self.assertEqual(loaded["papers"][0]["local_pdf_path"], str(pdf.resolve()))

    def test_after_saved_pdfs_at_limit_auto_submits_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdfs = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT):
                pdf = Path(tmp) / f"saved-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                pdfs.append(str(pdf))

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-limit"}),
            ) as submit_mock:
                result = asyncio.run(
                    server._after_saved_pdfs(
                        pdfs,
                        source="arxiv",
                        paper_id="batch",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                        custom_save_path_confirmed=True,
                    )
                )

            prompt = result["parse_prompt"]
            self.assertEqual(prompt["status"], "submitted")
            self.assertEqual(prompt["interaction"], "auto_parse_saved_pdfs")
            self.assertEqual(prompt["selected_indices"], list(range(1, server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)))
            self.assertEqual(prompt["recommended_tool"], "get_parse_job_status")
            self.assertEqual(prompt["parse_job"]["job_id"], "parse-limit")
            submit_mock.assert_awaited_once()
            self.assertEqual(
                submit_mock.await_args.kwargs["selected_indices"],
                ",".join(str(index) for index in range(1, server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)),
            )
            self.assertNotIn("app", result)

    def test_after_saved_pdfs_over_limit_creates_numbered_session_without_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdfs = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"saved-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                pdfs.append(str(pdf))

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_AUTO_OPEN_SELECTION_UI": "true",
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(side_effect=AssertionError("large batches should not auto-parse")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/after-saved"}),
            ):
                result = asyncio.run(
                    server._after_saved_pdfs(
                        pdfs,
                        source="arxiv",
                        paper_id="batch",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                        custom_save_path_confirmed=True,
                    )
                )

            self.assertEqual(result["status"], "downloaded")
            prompt = result["parse_prompt"]
            self.assertEqual(prompt["status"], "elicitation_unavailable")
            self.assertEqual(prompt["interaction"], "backend_session_numbered_selection")
            self.assertEqual(prompt["total"], server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            self.assertEqual(prompt["papers"][0]["reason"], "local_pdf_path")
            self.assertEqual(prompt["app"]["render_tool"], "render_paper_selection_app")
            self.assertEqual(prompt["app"]["resource_uri"], server.PAPER_SELECTION_WIDGET_URI)
            self.assertEqual(prompt["app"]["selection_token"], prompt["selection_token"])
            self.assertEqual(result["app"], prompt["app"])
            self.assertEqual(result["interaction"], "local_browser_checkbox")
            self.assertNotIn("_meta", result)
            self.assertTrue(result["parse_decision_required"])
            self.assertEqual(result["recommended_tool"], server.LOCAL_PAPER_SELECTION_TOOL)
            self.assertEqual(prompt["local_browser"]["interaction"], "local_browser_checkbox")

    def test_over_limit_prompt_opens_local_browser_when_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"default-no-open-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                papers.append(
                    {
                        "title": f"Default No Open {index + 1}",
                        "source": "local",
                        "paper_id": f"default-{index + 1}",
                        "local_pdf_path": str(pdf),
                    }
                )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/default"}),
            ):
                result = asyncio.run(
                    server._prompt_parse_saved_pdfs(
                        papers=papers,
                        query="default no open",
                        sources="local",
                        save_path=tmp,
                        ctx=None,
                        parse_execution="prompt",
                        custom_save_path_confirmed=True,
                    )
                )

        self.assertEqual(result["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertIn("app", result)
        self.assertEqual(result["selection_surface"]["surface"], "local_browser")
        self.assertNotIn("_meta", result)
        self.assertEqual(result["local_browser"]["interaction"], "local_browser_checkbox")

    def test_over_limit_prompt_app_only_confirmed_host_does_not_open_local_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"app-only-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                papers.append(
                    {
                        "title": f"App Only {index + 1}",
                        "source": "local",
                        "paper_id": f"app-only-{index + 1}",
                        "local_pdf_path": str(pdf),
                    }
                )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "app_only",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                side_effect=AssertionError("confirmed app_only host should not open localhost"),
            ):
                server.detect_host.cache_clear()
                result = asyncio.run(
                    server._prompt_parse_saved_pdfs(
                        papers=papers,
                        query="app only",
                        sources="local",
                        save_path=tmp,
                        ctx=None,
                        parse_execution="prompt",
                        custom_save_path_confirmed=True,
                    )
                )
                server.detect_host.cache_clear()

        self.assertEqual(result["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertIn("app", result)
        self.assertEqual(result["selection_surface"]["surface"], "mcp_app")
        self.assertNotIn("local_browser", result)

    def test_over_limit_prompt_app_only_tentative_host_keeps_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"claude-app-only-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                papers.append(
                    {
                        "title": f"Claude App Only {index + 1}",
                        "source": "local",
                        "paper_id": f"claude-app-only-{index + 1}",
                        "local_pdf_path": str(pdf),
                    }
                )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "claude_code_desktop",
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "app_only",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ) as open_mock:
                server.detect_host.cache_clear()
                result = asyncio.run(
                    server._prompt_parse_saved_pdfs(
                        papers=papers,
                        query="claude app only",
                        sources="local",
                        save_path=tmp,
                        ctx=None,
                        parse_execution="prompt",
                        custom_save_path_confirmed=True,
                    )
                )
                server.detect_host.cache_clear()

        self.assertEqual(result["selection_surface"]["surface"], "hybrid")
        self.assertEqual(result["selection_surface"]["app_widget_supported"], True)
        self.assertEqual(result["selection_surface"]["app_widget_confirmed"], False)
        self.assertIn("app", result)
        self.assertEqual(result["interaction"], "mcp_app")
        self.assertIn("local_browser", result)
        self.assertEqual(result["local_browser"]["status"], "ok")
        self.assertEqual(result["local_browser"]["interaction"], "local_browser_checkbox")
        self.assertTrue(result["local_browser"]["opened"])
        open_mock.assert_called_once()

    def test_over_limit_prompt_desktop_default_does_not_attach_local_browser_even_if_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"desktop-app-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                papers.append(
                    {
                        "title": f"Desktop App {index + 1}",
                        "source": "local",
                        "paper_id": f"desktop-app-{index + 1}",
                        "local_pdf_path": str(pdf),
                    }
                )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                side_effect=AssertionError("desktop app path should not open localhost"),
            ):
                server.detect_host.cache_clear()
                result = asyncio.run(
                    server._prompt_parse_saved_pdfs(
                        papers=papers,
                        query="desktop app",
                        sources="local",
                        save_path=tmp,
                        ctx=None,
                        parse_execution="prompt",
                        custom_save_path_confirmed=True,
                    )
                )
                server.detect_host.cache_clear()

        self.assertEqual(result["selection_surface"]["surface"], "mcp_app")
        self.assertIn("app", result)
        self.assertNotIn("local_browser", result)

    def test_after_saved_pdf_recent_directory_over_limit_prompts_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT):
                pdf = Path(tmp) / f"recent-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            new_pdf = Path(tmp) / "recent-new.pdf"
            new_pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_PROMPT": "true",
                    "PAPER_SEARCH_MCP_SAVED_PDF_BATCH_WINDOW_SECONDS": "3600",
                    "PAPER_SEARCH_MCP_AUTO_OPEN_SELECTION_UI": "true",
                    "PAPER_SEARCH_MCP_SELECTION_UI_MODE": "local_browser",
                },
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(side_effect=AssertionError("recent large batch should ask before parsing")),
            ), patch(
                "paper_search_mcp.server.open_paper_selection_page",
                new=AsyncMock(return_value={"interaction": "local_browser_checkbox", "url": "http://127.0.0.1/paper-selection/recent-pdf"}),
            ):
                result = asyncio.run(
                    server._after_saved_pdf(
                        str(new_pdf),
                        source="arxiv",
                        paper_id="2601.99999",
                        title="Recent New",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                        custom_save_path_confirmed=True,
                    )
                )

        prompt = result["parse_prompt"]
        self.assertEqual(prompt["trigger"], "saved_pdf_batch_threshold")
        self.assertEqual(prompt["total"], server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
        self.assertEqual(prompt["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertTrue(prompt["parse_decision_required"])
        self.assertEqual(result["app"]["selection_token"], prompt["selection_token"])
        self.assertEqual(result["interaction"], "local_browser_checkbox")
        self.assertNotIn("_meta", result)
        self.assertTrue(result["parse_decision_required"])
        self.assertEqual(prompt["local_browser"]["interaction"], "local_browser_checkbox")

    def test_recent_directory_prompt_restores_metadata_from_download_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"2601.{index + 1:05d}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                cache.record_download(
                    pdf_path=str(pdf),
                    source="arxiv",
                    paper_id=f"2601.{index + 1:05d}",
                    title=f"Recovered Title {index + 1}",
                    doi=f"10.48550/arXiv.2601.{index + 1:05d}",
                    cache_dir=tmp,
                )

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                papers = server._recent_saved_pdf_papers(tmp, window_seconds=3600)

        self.assertEqual(len(papers), server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
        self.assertEqual(papers[0]["title"], "Recovered Title 1")
        self.assertEqual(papers[0]["source"], "arxiv")
        self.assertEqual(papers[0]["doi"], "10.48550/arXiv.2601.00001")

    def test_after_saved_pdfs_over_limit_elicitation_accepts_and_submits_selection(self):
        class FakeData:
            selected_papers = ["1. Saved PDF [arxiv, n.d., 2601.00006]"]

        class FakeElicitation:
            action = "accept"
            data = FakeData()

        class FakeContext:
            def __init__(self):
                self.schema = None

            async def elicit(self, message, schema):
                self.schema = schema
                return FakeElicitation()

        with tempfile.TemporaryDirectory() as tmp:
            pdfs = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1):
                pdf = Path(tmp) / f"saved-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                pdfs.append(str(pdf))
            ctx = FakeContext()

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-elicited"}),
            ) as submit_mock:
                result = asyncio.run(
                    server._after_saved_pdfs(
                        pdfs,
                        source="arxiv",
                        paper_id="2601.00006",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=ctx,
                        custom_save_path_confirmed=True,
                    )
                )

            self.assertEqual(result["parse_prompt"]["interaction"], "elicitation")
            self.assertEqual(result["parse_prompt"]["selected_indices"], [1])
            self.assertEqual(result["parse_prompt"]["recommended_tool"], "get_parse_job_status")
            self.assertEqual(result["parse_prompt"]["parse_job"]["job_id"], "parse-elicited")
            submit_mock.assert_awaited_once()
            self.assertEqual(submit_mock.await_args.kwargs["selected_indices"], "1")
            schema_items = ctx.schema.model_json_schema()["properties"]["selected_papers"]["items"]
            self.assertIn("1. Saved PDF", schema_items["enum"][0])

    def test_read_source_paper_detects_saved_pdf_and_prompts(self):
        class FakeSearcher:
            def read_paper(self, paper_id, save_path):
                Path(save_path).mkdir(parents=True, exist_ok=True)
                (Path(save_path) / "read-saved.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
                return "extracted text"

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-read"}),
            ):
                result = asyncio.run(
                    server._read_source_paper(
                        FakeSearcher(),
                        source="arxiv",
                        paper_id="2601.00007",
                        save_path=tmp,
                        ctx=None,
                        custom_save_path_confirmed=True,
                    )
                )

            self.assertEqual(result["status"], "read")
            self.assertEqual(result["text"], "extracted text")
            prompt = result["saved_pdf_prompt"]["parse_prompt"]
            self.assertEqual(prompt["status"], "submitted")
            self.assertEqual(prompt["interaction"], "auto_parse_saved_pdfs")
            self.assertEqual(prompt["selected_indices"], [1])
            self.assertEqual(prompt["parse_job"]["job_id"], "parse-read")

    def test_search_papers_with_elicitation_accepts_selection(self):
        class FakeData:
            selected_papers = ["1. Selected Paper [arxiv, 2026, 2601.00003]"]

        class FakeElicitation:
            action = "accept"
            data = FakeData()

        class FakeContext:
            def __init__(self):
                self.schema = None

            async def elicit(self, message, schema):
                self.schema = schema
                return FakeElicitation()

        fake_session_result = {
            "status": "ok",
            "selection_token": "search_test_token",
            "query": "elicitation paper",
            "sources_used": ["arxiv"],
            "source_results": {"arxiv": 1},
            "errors": {},
            "papers": [
                {
                    "index": 1,
                    "title": "Selected Paper",
                    "authors": "A. Researcher",
                    "year": "2026",
                    "source": "arxiv",
                    "paper_id": "2601.00003",
                    "doi": "",
                    "pdf_url": "https://example.org/selected.pdf",
                    "url": "https://example.org/selected",
                    "parse_ready": True,
                    "reason": "direct_pdf_url",
                }
            ],
            "total": 1,
            "parse_ready_total": 1,
        }
        fake_parse_result = {
            "status": "ok",
            "selection_token": "search_test_token",
            "selected_indices": [1],
            "results": [{"status": "ok"}],
            "total": 1,
            "parsed": 1,
            "failed": 0,
            "skipped": 0,
        }
        ctx = FakeContext()

        with patch(
            "paper_search_mcp.server.search_papers_for_parsing",
            new=AsyncMock(return_value=fake_session_result),
        ), patch(
            "paper_search_mcp.server.parse_selected_papers",
            new=AsyncMock(return_value=fake_parse_result),
        ) as parse_mock:
            result = asyncio.run(
                server.search_papers_with_elicitation(
                    "elicitation paper",
                    sources="arxiv",
                    ctx=ctx,
                    mode="pypdf",
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["interaction"], "elicitation")
        self.assertEqual(result["selected_indices"], [1])
        parse_mock.assert_awaited_once()
        self.assertEqual(parse_mock.await_args.kwargs["selection_token"], "search_test_token")
        self.assertEqual(parse_mock.await_args.kwargs["selected_indices"], "1")
        schema_items = ctx.schema.model_json_schema()["properties"]["selected_papers"]["items"]
        self.assertIn("1. Selected Paper", schema_items["enum"][0])

    def test_search_papers_with_elicitation_falls_back_without_context(self):
        fake_session_result = {
            "status": "ok",
            "selection_token": "search_test_token",
            "query": "fallback paper",
            "papers": [
                {
                    "index": 1,
                    "title": "Fallback Paper",
                    "source": "arxiv",
                    "paper_id": "2601.00004",
                    "parse_ready": True,
                }
            ],
            "total": 1,
            "parse_ready_total": 1,
        }

        with patch(
            "paper_search_mcp.server.search_papers_for_parsing",
            new=AsyncMock(return_value=fake_session_result),
        ):
            result = asyncio.run(
                server.search_papers_with_elicitation(
                    "fallback paper",
                    sources="arxiv",
                    ctx=None,
                )
            )

        self.assertEqual(result["status"], "elicitation_unavailable")
        self.assertEqual(result["interaction"], "backend_session_numbered_selection")
        self.assertEqual(result["selection_token"], "search_test_token")

    def test_render_paper_selection_app_loads_saved_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="widget paper",
                sources="arxiv",
                papers=[
                    {
                        "title": "Widget Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00008",
                        "published_date": "2026-01-08",
                        "venue": "arXiv preprint",
                        "categories": ["cs.CV"],
                        "pdf_url": "https://example.org/widget.pdf",
                        "url": "https://example.org/widget",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                result = asyncio.run(
                    server.render_paper_selection_app(
                        selection_token=session["selection_token"],
                        mode="pypdf",
                    )
                )
                payload = unwrap_tool_result(result)

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(result.meta["ui"]["resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result.meta["ui/resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result.meta["openai/outputTemplate"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertTrue(result.meta["openai/widgetAccessible"])
        self.assertEqual(payload["interaction"], "mcp_app")
        self.assertEqual(payload["selection_token"], session["selection_token"])
        self.assertEqual(payload["papers"][0]["title"], "Widget Paper")
        self.assertEqual(payload["papers"][0]["parse_ready"], True)
        self.assertEqual(payload["papers"][0]["published_date"], "2026-01-08")
        self.assertEqual(payload["papers"][0]["publication_venue"], "Computer Vision and Pattern Recognition")
        self.assertEqual(payload["papers"][0]["original_url"], "https://example.org/widget")
        self.assertEqual(payload["_meta"]["output_template"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(payload["mode"], "pypdf")

    def test_registered_render_paper_selection_app_returns_top_level_widget_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="registered widget",
                sources="arxiv",
                papers=[
                    {
                        "title": "Registered Widget Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00009",
                        "pdf_url": "https://example.org/registered-widget.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                tool = server.mcp._tool_manager.get_tool("render_paper_selection_app")
                result = asyncio.run(
                    tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(result)

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(result.meta["ui"]["resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result.meta["ui/resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result.meta["openai/outputTemplate"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertTrue(result.meta["openai/widgetAccessible"])
        self.assertEqual(payload["selection_token"], session["selection_token"])
        self.assertEqual(payload["papers"][0]["title"], "Registered Widget Paper")

    def test_registered_render_selection_app_keeps_desktop_widget_without_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="desktop widget",
                sources="arxiv",
                papers=[
                    {
                        "title": "Desktop Widget Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00019",
                        "pdf_url": "https://example.org/desktop-widget.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_desktop",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ) as open_mock:
                server.detect_host.cache_clear()
                tool = server.mcp._tool_manager.get_tool("render_paper_selection_app")
                result = asyncio.run(
                    tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(result)
                server.detect_host.cache_clear()

        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(payload["app_widget_supported"])
        self.assertEqual(payload["detected_host"], "codex")
        self.assertEqual(payload["selection_surface"]["surface"], "mcp_app")
        self.assertNotIn("local_browser", payload)
        open_mock.assert_not_called()

    def test_registered_render_selection_app_returns_fallback_instructions_for_codex_vscode(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="vscode widget",
                sources="arxiv",
                papers=[
                    {
                        "title": "VS Code Widget Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00020",
                        "pdf_url": "https://example.org/vscode-widget.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ) as open_mock:
                server.detect_host.cache_clear()
                tool = server.mcp._tool_manager.get_tool("render_paper_selection_app")
                result = asyncio.run(
                    tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(result)
                server.detect_host.cache_clear()

        self.assertIsInstance(result, CallToolResult)
        self.assertFalse(payload["app_widget_supported"])
        self.assertEqual(payload["detected_host"], "codex_vscode")
        self.assertEqual(payload["selection_surface"]["surface"], "local_browser")
        self.assertEqual(payload["fallback_reason"], "host_without_mcp_app_sandbox")
        self.assertEqual(payload["fallback_tool"], "open_paper_selection_page")
        self.assertNotIn("local_browser", payload)
        open_mock.assert_not_called()

    def test_claude_code_desktop_render_meta_and_local_page_are_both_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="claude hybrid widget",
                sources="arxiv",
                papers=[
                    {
                        "title": "Claude Hybrid Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00022",
                        "pdf_url": "https://example.org/claude-hybrid.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "claude_code_desktop",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ) as open_mock:
                server.detect_host.cache_clear()
                render_tool = server.mcp._tool_manager.get_tool("render_paper_selection_app")
                render_result = asyncio.run(
                    render_tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                render_payload = unwrap_tool_result(render_result)

                local_tool = server.mcp._tool_manager.get_tool("open_paper_selection_page")
                local_result = asyncio.run(
                    local_tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                local_payload = unwrap_tool_result(local_result)
                server.detect_host.cache_clear()

        self.assertIsInstance(render_result, CallToolResult)
        self.assertEqual(render_result.meta["ui"]["resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(render_result.meta["ui/resourceUri"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(render_result.meta["openai/outputTemplate"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(render_payload["detected_host"], "claude_code_desktop")
        self.assertTrue(render_payload["app_widget_supported"])
        self.assertFalse(render_payload["selection_surface"]["app_widget_confirmed"])
        self.assertEqual(render_payload["selection_surface"]["surface"], "hybrid")
        self.assertEqual(local_payload["interaction"], "local_browser_checkbox")
        self.assertTrue(local_payload["opened"])
        open_mock.assert_called_once()

    def test_registered_open_selection_page_opens_local_fallback_for_codex_vscode(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="vscode local",
                sources="arxiv",
                papers=[
                    {
                        "title": "VS Code Local Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00021",
                        "pdf_url": "https://example.org/vscode-local.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                    "PAPER_SEARCH_MCP_CLIENT_HOST": "codex_vscode",
                },
            ), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ) as open_mock:
                server.detect_host.cache_clear()
                tool = server.mcp._tool_manager.get_tool("open_paper_selection_page")
                result = asyncio.run(
                    tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(result)
                server.detect_host.cache_clear()

        self.assertEqual(payload["interaction"], "local_browser_checkbox")
        self.assertTrue(payload["opened"])
        self.assertGreater(payload["local_port"], 0)
        open_mock.assert_called_once()

    def test_registered_paper_selection_state_survives_rerender(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="state widget",
                sources="arxiv",
                papers=[
                    {"title": "State Paper 1", "source": "arxiv", "paper_id": "2601.10001", "pdf_url": "https://example.org/1.pdf"},
                    {"title": "State Paper 2", "source": "arxiv", "paper_id": "2601.10002", "pdf_url": "https://example.org/2.pdf"},
                    {"title": "State Paper 3", "source": "arxiv", "paper_id": "2601.10003", "pdf_url": "https://example.org/3.pdf"},
                ],
                cache_dir=tmp,
            )
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                save_tool = server.mcp._tool_manager.get_tool("save_paper_selection_state")
                get_tool = server.mcp._tool_manager.get_tool("get_paper_selection_state")
                render_tool = server.mcp._tool_manager.get_tool("render_paper_selection_app")
                saved = asyncio.run(
                    save_tool.run(
                        {
                            "selection_token": session["selection_token"],
                            "selected_indices": "1,3",
                            "client_instance_id": "test-client",
                        },
                        convert_result=False,
                    )
                )
                loaded = asyncio.run(
                    get_tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                rendered = asyncio.run(
                    render_tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                saved_payload = unwrap_tool_result(saved)
                loaded_payload = unwrap_tool_result(loaded)
                rendered_payload = unwrap_tool_result(rendered)

        self.assertEqual(saved_payload["status"], "ok")
        self.assertEqual(loaded_payload["selected_indices"], [1, 3])
        self.assertEqual(loaded_payload["selected_indices_arg"], "1,3")
        self.assertTrue(loaded_payload["has_saved_state"])
        self.assertEqual(rendered_payload["persisted_selection"]["selected_indices"], [1, 3])
        self.assertEqual(rendered_payload["persisted_selection"]["selected_indices_arg"], "1,3")

    def test_registered_paper_selection_state_persists_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="state widget empty",
                sources="arxiv",
                papers=[
                    {"title": "State Paper 1", "source": "arxiv", "paper_id": "2601.10001", "pdf_url": "https://example.org/1.pdf"},
                    {"title": "State Paper 2", "source": "arxiv", "paper_id": "2601.10002", "pdf_url": "https://example.org/2.pdf"},
                ],
                cache_dir=tmp,
            )
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                save_tool = server.mcp._tool_manager.get_tool("save_paper_selection_state")
                render_tool = server.mcp._tool_manager.get_tool("render_paper_selection_app")
                saved = asyncio.run(
                    save_tool.run(
                        {
                            "selection_token": session["selection_token"],
                            "selected_indices": "",
                            "client_instance_id": "test-client",
                        },
                        convert_result=False,
                    )
                )
                rendered = asyncio.run(
                    render_tool.run(
                        {"selection_token": session["selection_token"]},
                        convert_result=False,
                    )
                )
                saved_payload = unwrap_tool_result(saved)
                rendered_payload = unwrap_tool_result(rendered)

        self.assertEqual(saved_payload["status"], "ok")
        self.assertEqual(saved_payload["selected_indices"], [])
        self.assertTrue(rendered_payload["persisted_selection"]["has_saved_state"])
        self.assertEqual(rendered_payload["persisted_selection"]["selected_indices"], [])

    def test_registered_paper_selection_state_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="state widget stale",
                sources="arxiv",
                papers=[
                    {"title": "State Paper 1", "source": "arxiv", "paper_id": "2601.10001", "pdf_url": "https://example.org/1.pdf"},
                    {"title": "State Paper 2", "source": "arxiv", "paper_id": "2601.10002", "pdf_url": "https://example.org/2.pdf"},
                ],
                cache_dir=tmp,
            )
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}):
                save_tool = server.mcp._tool_manager.get_tool("save_paper_selection_state")
                stale = asyncio.run(
                    save_tool.run(
                        {
                            "selection_token": session["selection_token"],
                            "selected_indices": "1",
                            "selection_revision": "old-render",
                        },
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(stale)
                loaded = cache.read_selection_ui_state(session["selection_token"], cache_dir=tmp)

        self.assertEqual(payload["status"], "stale_selection")
        self.assertEqual(loaded, {})

    def test_registered_open_paper_url_uses_session_url_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="open url",
                sources="arxiv",
                papers=[
                    {
                        "title": "Open URL Paper",
                        "source": "arxiv",
                        "paper_id": "2601.10004",
                        "url": "https://arxiv.org/abs/2601.10004",
                        "pdf_url": "https://arxiv.org/pdf/2601.10004",
                    }
                ],
                cache_dir=tmp,
            )
            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.utils.open_url_in_host",
                return_value=True,
            ) as open_mock:
                tool = server.mcp._tool_manager.get_tool("open_paper_url_in_browser")
                result = asyncio.run(
                    tool.run(
                        {
                            "selection_token": session["selection_token"],
                            "paper_index": 1,
                            "url_kind": "pdf",
                        },
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(result)

        self.assertTrue(payload["opened"])
        self.assertEqual(payload["url"], "https://arxiv.org/pdf/2601.10004")
        open_mock.assert_called_once_with("https://arxiv.org/pdf/2601.10004")

    def test_registered_tools_do_not_expose_confirmation_token_minting(self):
        tools = asyncio.run(server.mcp.list_tools())
        names = {tool.name for tool in tools}

        self.assertIn("confirm_paper_selection", names)
        self.assertIn("download_confirmed_paper_selection", names)
        self.assertNotIn("create_selection_confirmation_token", names)
        self.assertNotIn("create_paper_selection_confirmation", names)

    def test_registered_download_confirmed_selection_downloads_selected_over_limit_without_exposed_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Confirmed Paper {index + 1}",
                    "source": "arxiv",
                    "paper_id": f"2610.{index + 1:05d}",
                    "pdf_url": f"https://example.org/confirmed-{index + 1}.pdf",
                }
                for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            ]
            session = cache.create_search_session(
                query="confirmed many",
                sources="arxiv",
                papers=papers,
                metadata={"selection_semantics": server.SELECTION_SEMANTICS_DOWNLOAD_ONLY},
                cache_dir=tmp,
            )

            async def fake_download_wrapper(**kwargs):
                pdf = Path(tmp) / f"confirmed-{kwargs['index']}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                return {
                    "index": kwargs["index"],
                    "status": "downloaded",
                    "candidate": {"title": f"Confirmed Paper {kwargs['index']}"},
                    "download_method": "test",
                    "pdf_path": str(pdf),
                    "valid_pdf": True,
                }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._download_selected_session_paper_wrapper",
                new=AsyncMock(side_effect=fake_download_wrapper),
            ) as download_mock:
                tool = server.mcp._tool_manager.get_tool("download_confirmed_paper_selection")
                result = asyncio.run(
                    tool.run(
                        {
                            "selection_token": session["selection_token"],
                            "selected_indices": "2,4",
                            "save_path": tmp,
                            "custom_save_path_confirmed": True,
                            "parse_execution": "none",
                        },
                        convert_result=False,
                    )
                )
                payload = unwrap_tool_result(result)
                loaded = cache.get_search_session(session["selection_token"], cache_dir=tmp)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["downloaded"], 2)
        self.assertEqual(payload["parse_execution"], "none")
        self.assertEqual(payload["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertNotIn("parse_job", payload["parse_prompt"])
        self.assertEqual(download_mock.await_count, 2)
        self.assertEqual([call.kwargs["index"] for call in download_mock.await_args_list], [2, 4])
        self.assertEqual(loaded["metadata"]["confirmed_via"], "mcp_app")
        self.assertEqual(loaded["metadata"]["confirmed_selected_indices"], "2,4")

    def test_selection_confirmation_token_is_bound_to_save_path(self):
        from paper_search_mcp.selection_confirmation import (
            consume_selection_confirmation_token,
            create_selection_confirmation_token,
        )

        with tempfile.TemporaryDirectory() as tmp:
            papers = [
                {
                    "title": f"Bound Paper {index + 1}",
                    "source": "arxiv",
                    "paper_id": f"2611.{index + 1:05d}",
                    "pdf_url": f"https://example.org/bound-{index + 1}.pdf",
                }
                for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
            ]
            session = cache.create_search_session(
                query="bound confirmation",
                sources="arxiv",
                papers=papers,
                cache_dir=tmp,
            )
            created = create_selection_confirmation_token(
                selection_token=session["selection_token"],
                selected_indices="1,2",
                action="download",
                save_path="C:\\papers\\first",
                cache_dir=tmp,
            )
            consumed = consume_selection_confirmation_token(
                selection_token=session["selection_token"],
                selected_indices="1,2",
                confirmation_token=str(created.get("selection_confirmation_token") or ""),
                confirmed_via="local_browser",
                action="download",
                save_path="C:\\papers\\second",
                cache_dir=tmp,
            )
            loaded = cache.get_search_session(session["selection_token"], cache_dir=tmp)

        self.assertEqual(created["status"], "ok")
        self.assertEqual(consumed["status"], "invalid_confirmation")
        self.assertEqual(consumed["downloaded"], 0)
        self.assertNotIn("confirmed_selected_indices", loaded["metadata"])

    def test_registered_download_and_parse_selected_tool_submits_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = cache.create_search_session(
                query="registered download parse",
                sources="arxiv",
                papers=[
                    {
                        "title": "Registered Download Parse Paper",
                        "source": "arxiv",
                        "paper_id": "2601.00010",
                        "pdf_url": "https://example.org/registered-download-parse.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.tools.core._run_submit_parse_job",
                new=AsyncMock(
                    return_value={
                        "status": "submitted",
                        "job_id": "registered-download-parse",
                    }
                ),
            ) as submit_mock:
                tool = server.mcp._tool_manager.get_tool(
                    "download_and_parse_selected_papers"
                )
                result = asyncio.run(
                    tool.run(
                        {
                            "selection_token": session["selection_token"],
                            "selected_indices": "1",
                        },
                        convert_result=False,
                    )
                )

        payload = unwrap_tool_result(result)
        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["job_id"], "registered-download-parse")
        self.assertEqual(payload["interaction"], "download_and_parse_selected")
        self.assertEqual(
            payload["selection_semantics"],
            server.SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
        )
        submit_mock.assert_awaited_once()
        self.assertEqual(
            submit_mock.await_args.kwargs["selection_token"],
            session["selection_token"],
        )
        self.assertEqual(submit_mock.await_args.kwargs["selected_indices"], "1")

    def test_paper_selection_widget_contains_checkbox_and_tool_call(self):
        html = asyncio.run(server.paper_selection_widget())
        self.assertIn('type="checkbox"', html)
        self.assertIn("submit_parse_job", html)
        self.assertIn("download_confirmed_paper_selection", html)
        self.assertNotIn("open_paper_selection_page", html)
        self.assertIn("save_paper_selection_state", html)
        self.assertIn("persisted_selection", html)
        self.assertIn("selection_revision", html)
        self.assertIn("applyPersistedSelection", html)
        self.assertIn("open_paper_url_in_browser", html)
        self.assertIn("dismiss_parse_prompt", html)
        self.assertIn("skip-mineru", html)
        self.assertNotIn("countdown-chip", html)
        self.assertNotIn("MinerU optional", html)
        self.assertIn("selectionTimeoutTimer", html)
        self.assertIn("selection_timeout_seconds", html)
        self.assertIn("celebration-burst", html)
        self.assertIn("download-skeleton", html)
        self.assertIn("decision-panel", html)
        self.assertIn("window.openai?.callTool", html)
        self.assertIn("unwrapToolOutput", html)
        self.assertIn("value?.result", html)
        self.assertGreaterEqual(html.count("const downloadAndParse"), 1)

    def test_paper_selection_widget_prompts_parse_after_download_only_completion(self):
        html = asyncio.run(server.paper_selection_widget())
        self.assertIn("function isParseReadyPrompt(prompt)", html)
        self.assertIn("Number(prompt.parse_ready_total || 0) > 0", html)
        self.assertIn("Boolean(recommended)", html)
        self.assertIn("isParseReadyPrompt(prompt)", html)
        self.assertNotIn("body.parse_prompt.parse_decision_required) {\n            startParseDecision", html)

    def test_open_paper_selection_page_serves_checkbox_page_and_posts_selection(self):
        async def fake_download_and_parse(**kwargs):
            return {
                "status": "submitted",
                "job_id": "parse-test",
                "interaction": "download_and_parse_selected",
                "selection_token": kwargs["selection_token"],
                "selected_indices": kwargs["selected_indices"],
            }

        with patch("paper_search_mcp.server.webbrowser.open", return_value=True), patch(
            "paper_search_mcp.server.download_and_parse_selected_papers",
            new=AsyncMock(side_effect=fake_download_and_parse),
        ) as parse_mock:
            result = asyncio.run(
                server.open_paper_selection_page(
                    selection_token="local-test-token",
                    papers=[
                        {
                            "index": 1,
                            "title": "Local Checkbox Paper",
                            "source": "arxiv",
                            "paper_id": "2606.01494v1",
                            "published_date": "2026-06-14",
                            "publication_venue": "Computer Vision and Pattern Recognition",
                            "original_url": "https://arxiv.org/abs/2606.01494v1",
                            "local_pdf_path": r"C:\tmp\paper.pdf",
                            "parse_ready": True,
                            "reason": "local_pdf_path",
                        }
                    ],
                    open_browser=True,
                )
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["interaction"], "local_browser_checkbox")
            self.assertTrue(result["opened"])

            html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
            self.assertIn('type="checkbox"', html)
            self.assertIn("Local Checkbox Paper", html)
            self.assertIn("Published", html)
            self.assertIn("2026-06-14", html)
            self.assertIn("Venue", html)
            self.assertIn("Computer Vision and Pattern Recognition", html)
            self.assertNotIn("arXiv preprint", html)
            self.assertIn("Original URL", html)
            self.assertIn("https://arxiv.org/abs/2606.01494v1", html)
            self.assertNotIn("local_pdf_path", html)
            self.assertNotIn(r"C:\tmp\paper.pdf", html)
            confirmation_token = _local_page_confirmation_token(html)
            self.assertTrue(confirmation_token)

            bad_request = urllib.request.Request(
                result["url"].replace("/paper-selection/", "/api/parse-selection/"),
                data=json.dumps({"selected_indices": "1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as bad_response:
                urllib.request.urlopen(bad_request, timeout=5)
            self.assertEqual(bad_response.exception.code, 403)

            request = urllib.request.Request(
                result["url"].replace("/paper-selection/", "/api/parse-selection/"),
                data=json.dumps(
                    {
                        "selected_indices": "1",
                        "confirmation_token": confirmation_token,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            body = json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))

        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["job_id"], "parse-test")
        self.assertEqual(body["interaction"], "download_and_parse_selected")
        self.assertEqual(body["selection_token"], "local-test-token")
        self.assertEqual(body["selected_indices"], "1")
        parse_mock.assert_awaited_once()

    def test_modular_local_selection_page_posts_to_download_and_parse(self):
        from paper_search_mcp.ui import server as ui_server

        async def fake_download_and_parse(**kwargs):
            return {
                "status": "submitted",
                "job_id": "modular-parse-test",
                "interaction": "download_and_parse_selected",
                "selection_token": kwargs["selection_token"],
                "selected_indices": kwargs["selected_indices"],
            }

        with patch("paper_search_mcp.utils.open_url_in_host", return_value=True), patch(
            "paper_search_mcp.tools.core._run_download_and_parse_selected_papers",
            new=AsyncMock(side_effect=fake_download_and_parse),
        ) as parse_mock:
            result = asyncio.run(
                ui_server.open_paper_selection_page(
                    selection_token="modular-local-token",
                    papers=[
                        {
                            "index": 1,
                            "title": "Modular Local Paper",
                            "source": "arxiv",
                            "paper_id": "2606.01495v1",
                            "pdf_url": "https://example.org/modular.pdf",
                            "parse_ready": True,
                            "reason": "direct_pdf_url",
                        }
                    ],
                    open_browser=True,
                    selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
                    parse_execution="background",
                )
            )

            html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
            confirmation_token = _local_page_confirmation_token(html)
            request = urllib.request.Request(
                result["url"].replace("/paper-selection/", "/api/parse-selection/"),
                data=json.dumps(
                    {
                        "selected_indices": "1",
                        "confirmation_token": confirmation_token,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            body = json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))

        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["job_id"], "modular-parse-test")
        self.assertEqual(body["interaction"], "download_and_parse_selected")
        self.assertEqual(body["selection_token"], "modular-local-token")
        self.assertEqual(body["selected_indices"], "1")
        parse_mock.assert_awaited_once()

    def test_modular_local_download_page_requires_click_and_returns_parse_prompt(self):
        from paper_search_mcp.ui import server as ui_server

        async def fake_download_only(**kwargs):
            return {
                "status": "ok",
                "selection_token": kwargs["selection_token"],
                "selected_indices": [1],
                "downloaded": 1,
                "failed": 0,
                "parse_prompt": {
                    "status": "ok",
                    "parse_decision_required": True,
                    "recommended_tool": "submit_parse_job",
                    "recommended_selected_indices": "all",
                    "default_parse_selected_indices": "all",
                },
                "message": "Saved 1 PDFs. MinerU parsing was not started.",
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ):
            session = cache.create_search_session(
                query="modular download page",
                sources="arxiv",
                papers=[
                    {
                        "index": 1,
                        "title": "Modular Download Paper",
                        "source": "arxiv",
                        "paper_id": "2606.01496v1",
                        "pdf_url": "https://example.org/download.pdf",
                        "parse_ready": True,
                        "reason": "direct_pdf_url",
                    }
                ],
                cache_dir=tmp,
            )
            with patch("paper_search_mcp.utils.open_url_in_host", return_value=True), patch(
                "paper_search_mcp.tools.orchestration._run_download_selected_papers",
                new=AsyncMock(side_effect=fake_download_only),
            ) as download_mock:
                result = asyncio.run(
                    ui_server.open_paper_selection_page(
                        selection_token=session["selection_token"],
                        papers=[
                            {
                                "index": 1,
                                "title": "Modular Download Paper",
                                "source": "arxiv",
                                "paper_id": "2606.01496v1",
                                "pdf_url": "https://example.org/download.pdf",
                                "parse_ready": True,
                                "reason": "direct_pdf_url",
                            }
                        ],
                        open_browser=True,
                        selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                        parse_execution="none",
                    )
                )

                html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
                confirmation_token = _local_page_confirmation_token(html)
                request = urllib.request.Request(
                    result["url"].replace("/paper-selection/", "/api/download-selection/"),
                    data=json.dumps(
                        {
                            "selected_indices": "1",
                            "confirmation_token": confirmation_token,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                body = json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))
                loaded = cache.get_search_session(session["selection_token"], cache_dir=tmp)

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["selection_token"], session["selection_token"])
        self.assertEqual(body["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(body["parse_prompt"]["recommended_selected_indices"], "all")
        download_mock.assert_awaited_once()
        self.assertEqual(download_mock.await_args.kwargs["large_batch_selection"], "never")
        self.assertTrue(download_mock.await_args.kwargs["bypass_large_batch_selection"])
        self.assertEqual(loaded["metadata"]["confirmed_via"], "local_browser")
        self.assertEqual(loaded["metadata"]["confirmed_selected_indices"], "1")

    def test_modular_local_download_page_displays_download_ready_candidates(self):
        from paper_search_mcp.ui import server as ui_server

        papers = [
            {
                "index": 1,
                "title": "Ready Paper 1",
                "source": "arxiv",
                "paper_id": "2606.01401",
                "pdf_url": "https://arxiv.org/pdf/2606.01401",
                "parse_ready": True,
                "download_ready": True,
            },
            {
                "index": 2,
                "title": "DOI Landing Page",
                "source": "openalex",
                "paper_id": "W123",
                "doi": "10.1016/j.example.2026.01.001",
                "pdf_url": "https://doi.org/10.1016/j.example.2026.01.001",
                "parse_ready": False,
                "download_ready": False,
            },
            {
                "index": 3,
                "title": "Ready Paper 3",
                "source": "arxiv",
                "paper_id": "2606.01403",
                "pdf_url": "https://arxiv.org/pdf/2606.01403",
                "parse_ready": True,
                "download_ready": True,
            },
        ]

        with patch("paper_search_mcp.utils.open_url_in_host", return_value=True):
            result = asyncio.run(
                ui_server.open_paper_selection_page(
                    selection_token="modular-download-ready-token",
                    papers=papers,
                    open_browser=True,
                    selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                    parse_execution="none",
                    requested_count=2,
                    full_total=len(papers),
                )
            )

        self.assertEqual([paper["index"] for paper in result["papers"]], [1, 2])
        self.assertEqual([paper["source_index"] for paper in result["papers"]], [1, 3])
        self.assertEqual(result["display_total"], 2)
        self.assertEqual(result["full_total"], 3)
        html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
        self.assertIn("Ready Paper 1", html)
        self.assertIn("Ready Paper 3", html)
        self.assertNotIn("DOI Landing Page", html)

    def test_modular_local_download_selection_timeout_blocks_late_submit(self):
        from paper_search_mcp.ui import server as ui_server

        async def fake_download_only(**kwargs):
            return {
                "status": "ok",
                "selection_token": kwargs["selection_token"],
                "selected_indices": [1],
                "downloaded": 1,
                "failed": 0,
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ):
            session = cache.create_search_session(
                query="modular timeout download",
                sources="arxiv",
                papers=[
                    {
                        "index": 1,
                        "title": "Timeout Download Paper",
                        "source": "arxiv",
                        "paper_id": "2606.01499v1",
                        "pdf_url": "https://example.org/timeout.pdf",
                        "parse_ready": True,
                    }
                ],
                cache_dir=tmp,
            )
            with patch("paper_search_mcp.utils.open_url_in_host", return_value=True), patch(
                "paper_search_mcp.tools.orchestration._run_download_selected_papers",
                new=AsyncMock(side_effect=fake_download_only),
            ) as download_mock:
                result = asyncio.run(
                    ui_server.open_paper_selection_page(
                        selection_token=session["selection_token"],
                        papers=[
                        {
                            "index": 1,
                            "title": "Timeout Download Paper",
                            "source": "arxiv",
                            "paper_id": "2606.01499v1",
                            "pdf_url": "https://example.org/timeout.pdf",
                            "parse_ready": True,
                        }
                        ],
                        open_browser=True,
                        selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                        parse_execution="none",
                    )
                )

                html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
                confirmation_token = _local_page_confirmation_token(html)
                timeout_request = urllib.request.Request(
                    result["url"].replace("/paper-selection/", "/api/download-selection-timeout/"),
                    data=json.dumps({"reason": "timeout"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                timeout_body = json.loads(
                    urllib.request.urlopen(timeout_request, timeout=5).read().decode("utf-8")
                )
                late_request = urllib.request.Request(
                    result["url"].replace("/paper-selection/", "/api/download-selection/"),
                    data=json.dumps(
                        {
                            "selected_indices": "1",
                            "confirmation_token": confirmation_token,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as late_response:
                    urllib.request.urlopen(late_request, timeout=5)
                late_body = json.loads(late_response.exception.read().decode("utf-8"))

        self.assertEqual(timeout_body["status"], "selection_expired")
        self.assertTrue(timeout_body["terminal"])
        self.assertEqual(late_response.exception.code, 410)
        self.assertEqual(late_body["status"], "selection_expired")
        download_mock.assert_not_awaited()

    def test_modular_internal_download_only_returns_default_all_parse_prompt(self):
        from paper_search_mcp.tools import orchestration

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "modular-internal.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="modular internal download",
                sources="arxiv",
                papers=[
                    {
                        "title": "Modular Internal Download Paper",
                        "source": "arxiv",
                        "paper_id": "2606.01497v1",
                        "pdf_url": "https://example.org/internal.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            async def fake_download_wrapper(**kwargs):
                return {
                    "index": kwargs["index"],
                    "status": "downloaded",
                    "candidate": {
                        "title": "Modular Internal Download Paper",
                        "source": "arxiv",
                        "paper_id": "2606.01497v1",
                    },
                    "download_method": "test",
                    "pdf_path": str(pdf),
                    "valid_pdf": True,
                }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._download_selected_session_paper_wrapper",
                new=AsyncMock(side_effect=fake_download_wrapper),
            ), patch(
                "paper_search_mcp.tools.orchestration._attach_local_selection_ui",
                new=AsyncMock(return_value={}),
            ) as attach_mock:
                result = asyncio.run(
                    orchestration._run_download_selected_papers(
                        selection_token=session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )
                pending_state = cache.read_parse_prompt_state(
                    session["selection_token"], cache_dir=tmp
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 1)
        self.assertFalse(result["parse_prompt"]["parse_decision_required"])
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["default_parse_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["download_selection_token"], session["selection_token"])
        self.assertEqual(pending_state, {})
        attach_mock.assert_not_awaited()

    def test_modular_internal_partial_download_still_prompts_for_successful_pdfs(self):
        from paper_search_mcp.tools import orchestration

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "partial-success.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="partial download",
                sources="arxiv,openalex",
                papers=[
                    {
                        "title": "Partial Success Paper",
                        "source": "arxiv",
                        "paper_id": "2606.01500v1",
                        "pdf_url": "https://example.org/partial-success.pdf",
                    },
                    {
                        "title": "Partial Failed Paper",
                        "source": "openalex",
                        "paper_id": "W123",
                        "doi": "10.1016/j.example.2026.01.001",
                        "pdf_url": "https://doi.org/10.1016/j.example.2026.01.001",
                    },
                ],
                cache_dir=tmp,
            )

            async def fake_download_wrapper(**kwargs):
                if kwargs["index"] == 1:
                    return {
                        "index": 1,
                        "status": "downloaded",
                        "candidate": {
                            "title": "Partial Success Paper",
                            "source": "arxiv",
                            "paper_id": "2606.01500v1",
                        },
                        "download_method": "test",
                        "pdf_path": str(pdf),
                        "valid_pdf": True,
                    }
                return {
                    "index": 2,
                    "status": "download_failed",
                    "candidate": {
                        "title": "Partial Failed Paper",
                        "source": "openalex",
                        "paper_id": "W123",
                        "doi": "10.1016/j.example.2026.01.001",
                    },
                    "message": "Download failed after OA fallback chain.",
                }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._download_selected_session_paper_wrapper",
                new=AsyncMock(side_effect=fake_download_wrapper),
            ), patch(
                "paper_search_mcp.tools.orchestration._attach_local_selection_ui",
                new=AsyncMock(return_value={}),
            ):
                result = asyncio.run(
                    orchestration._run_download_selected_papers(
                        selection_token=session["selection_token"],
                        selected_indices="1-2",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["successful_pdf_count"], 1)
        self.assertIn("parse_prompt", result)
        self.assertEqual(result["parse_prompt"]["parse_ready_total"], 1)
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["papers"][0]["title"], "Partial Success Paper")

    def test_modular_internal_download_only_timeout_blocks_widget_promotion(self):
        from paper_search_mcp.tools import orchestration

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "modular-timeout.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            session = cache.create_search_session(
                query="modular timeout",
                sources="arxiv",
                papers=[
                    {
                        "title": "Modular Timeout Paper",
                        "source": "arxiv",
                        "paper_id": "2606.09992v1",
                        "pdf_url": "https://example.org/modular-timeout.pdf",
                    }
                ],
                cache_dir=tmp,
            )

            async def fake_download_wrapper(**kwargs):
                return {
                    "index": kwargs["index"],
                    "status": "downloaded",
                    "candidate": {
                        "title": "Modular Timeout Paper",
                        "source": "arxiv",
                        "paper_id": "2606.09992v1",
                    },
                    "download_method": "test",
                    "pdf_path": str(pdf),
                    "valid_pdf": True,
                }

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.tools.orchestration._download_selected_session_paper_wrapper",
                new=AsyncMock(side_effect=fake_download_wrapper),
            ):
                first = asyncio.run(
                    orchestration._run_download_selected_papers(
                        selection_token=session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )
                prompt = first["parse_prompt"]
                dismissed = dismiss_parse_prompt_state(
                    session["selection_token"],
                    prompt_id=prompt["prompt_id"],
                    reason="timeout",
                )
                second = asyncio.run(
                    orchestration._run_download_selected_papers(
                        selection_token=session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                        custom_save_path_confirmed=True,
                        parse_execution="none",
                    )
                )

        self.assertEqual(dismissed["status"], "timed_out_no_parse")
        self.assertEqual(second["parse_prompt"]["status"], "timed_out_no_parse")
        self.assertFalse(second["parse_prompt"]["parse_decision_required"])
        self.assertNotIn("_meta", second)
        self.assertNotEqual(second.get("interaction"), "mcp_app")

    def test_modular_local_download_page_parses_downloaded_selection_in_same_page(self):
        from paper_search_mcp.ui import server as ui_server

        async def fake_download_only(**kwargs):
            return {
                "status": "ok",
                "selection_token": kwargs["selection_token"],
                "selected_indices": [1],
                "downloaded": 1,
                "failed": 0,
                "parse_prompt": {
                    "status": "ok",
                    "selection_token": "downloaded-parse-token",
                    "parse_ready_total": 1,
                    "parse_decision_required": True,
                    "recommended_tool": "submit_parse_job",
                    "recommended_selected_indices": "all",
                    "default_parse_selected_indices": "all",
                },
                "message": "Saved 1 PDFs. MinerU parsing was not started.",
            }

        async def fake_download_and_parse(**kwargs):
            return {
                "status": "submitted",
                "job_id": "same-page-parse-job",
                "interaction": "download_and_parse_selected",
                "selection_token": kwargs["selection_token"],
                "selected_indices": kwargs["selected_indices"],
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ):
            session = cache.create_search_session(
                query="same page download",
                sources="arxiv",
                papers=[
                    {
                        "index": 1,
                        "title": "Same Page Download Paper",
                        "source": "arxiv",
                        "paper_id": "2606.01498v1",
                        "pdf_url": "https://example.org/same-page.pdf",
                        "parse_ready": True,
                        "reason": "direct_pdf_url",
                    }
                ],
                cache_dir=tmp,
            )
            with patch("paper_search_mcp.utils.open_url_in_host", return_value=True), patch(
                "paper_search_mcp.tools.orchestration._run_download_selected_papers",
                new=AsyncMock(side_effect=fake_download_only),
            ) as download_mock, patch(
                "paper_search_mcp.tools.core._run_download_and_parse_selected_papers",
                new=AsyncMock(side_effect=fake_download_and_parse),
            ) as parse_mock:
                result = asyncio.run(
                    ui_server.open_paper_selection_page(
                        selection_token=session["selection_token"],
                        papers=[
                        {
                            "index": 1,
                            "title": "Same Page Download Paper",
                            "source": "arxiv",
                            "paper_id": "2606.01498v1",
                            "pdf_url": "https://example.org/same-page.pdf",
                            "parse_ready": True,
                            "reason": "direct_pdf_url",
                        }
                        ],
                        open_browser=True,
                        selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
                        parse_execution="background",
                    )
                )

                html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
                confirmation_token = _local_page_confirmation_token(html)
                download_request = urllib.request.Request(
                    result["url"].replace("/paper-selection/", "/api/download-selection/"),
                    data=json.dumps(
                        {
                            "selected_indices": "1",
                            "confirmation_token": confirmation_token,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                download_body = json.loads(
                    urllib.request.urlopen(download_request, timeout=5).read().decode("utf-8")
                )
                parse_request = urllib.request.Request(
                    result["url"].replace(
                        "/paper-selection/", "/api/parse-downloaded-selection/"
                    ),
                    data=json.dumps(
                        {
                            "parse_selection_token": download_body["parse_prompt"]["selection_token"],
                            "selected_indices": download_body["parse_prompt"][
                                "default_parse_selected_indices"
                            ],
                            "confirmation_token": download_body["confirmation_token"],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                parse_body = json.loads(
                    urllib.request.urlopen(parse_request, timeout=5).read().decode("utf-8")
                )

        self.assertEqual(download_body["status"], "ok")
        self.assertIn("confirmation_token", download_body)
        self.assertEqual(parse_body["status"], "submitted")
        self.assertEqual(parse_body["job_id"], "same-page-parse-job")
        self.assertEqual(parse_body["selection_token"], "downloaded-parse-token")
        self.assertEqual(parse_body["selected_indices"], "all")
        download_mock.assert_awaited_once()
        parse_mock.assert_awaited_once()

    def test_modular_local_parse_prompt_timeout_endpoint_is_idempotent(self):
        from paper_search_mcp.ui import server as ui_server

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                "PAPER_SEARCH_MCP_PARSE_PROMPT_TIMEOUT_SECONDS": "120",
            },
        ), patch("paper_search_mcp.utils.open_url_in_host", return_value=True):
            session = cache.create_search_session(
                query="local timeout endpoint",
                sources="arxiv",
                papers=[
                    {
                        "title": "Local Timeout Endpoint Paper",
                        "source": "arxiv",
                        "paper_id": "2606.09993v1",
                        "local_pdf_path": str(Path(tmp) / "local-timeout.pdf"),
                    }
                ],
                cache_dir=tmp,
            )
            parse_session = cache.create_search_session(
                query="local timeout endpoint parse",
                sources="local",
                papers=[
                    {
                        "title": "Local Timeout Endpoint Paper",
                        "source": "arxiv",
                        "paper_id": "2606.09993v1",
                        "local_pdf_path": str(Path(tmp) / "local-timeout.pdf"),
                    }
                ],
                cache_dir=tmp,
            )
            prompt_state = {
                "download_selection_token": session["selection_token"],
                "parse_selection_token": parse_session["selection_token"],
                "prompt_id": "parse_prompt_local_timeout",
                "state": "pending",
                "timeout_seconds": 120,
                "timeout_action": "no_parse",
            }
            cache.write_parse_prompt_state(session["selection_token"], prompt_state, cache_dir=tmp)

            result = asyncio.run(
                ui_server.open_paper_selection_page(
                    selection_token=session["selection_token"],
                    papers=[
                        {
                            "index": 1,
                            "title": "Local Timeout Endpoint Paper",
                            "source": "arxiv",
                            "paper_id": "2606.09993v1",
                            "parse_ready": True,
                        }
                    ],
                    open_browser=True,
                    selection_semantics=server.SELECTION_SEMANTICS_DOWNLOAD_ONLY,
                    parse_execution="none",
                )
            )
            html = urllib.request.urlopen(result["url"], timeout=5).read().decode("utf-8")
            self.assertIn("/api/parse-prompt-timeout/", html)

            page = ui_server._LOCAL_SELECTION_PAGES[result["page_id"]]
            page["last_parse_prompt"] = {
                "selection_token": parse_session["selection_token"],
                "download_selection_token": session["selection_token"],
                "prompt_id": "parse_prompt_local_timeout",
                "timeout_seconds": 120,
            }
            timeout_request = urllib.request.Request(
                result["url"].replace("/paper-selection/", "/api/parse-prompt-timeout/"),
                data=json.dumps(
                    {
                        "download_selection_token": session["selection_token"],
                        "prompt_id": "parse_prompt_local_timeout",
                        "reason": "timeout",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            first = json.loads(urllib.request.urlopen(timeout_request, timeout=5).read().decode("utf-8"))
            second_request = urllib.request.Request(
                result["url"].replace("/paper-selection/", "/api/parse-prompt-timeout/"),
                data=json.dumps(
                    {
                        "download_selection_token": session["selection_token"],
                        "prompt_id": "parse_prompt_local_timeout",
                        "reason": "timeout",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            second = json.loads(urllib.request.urlopen(second_request, timeout=5).read().decode("utf-8"))

        self.assertEqual(first["status"], "timed_out_no_parse")
        self.assertTrue(first["terminal"])
        self.assertFalse(first["parse_decision_required"])
        self.assertIn("120 seconds", first["message"])
        self.assertEqual(second["status"], "timed_out_no_parse")
        self.assertEqual(second["prompt_id"], first["prompt_id"])

    def test_local_selection_page_download_and_parse_uses_clean_parse_script(self):
        html = server._render_local_selection_html(
            "local-parse-page",
            {
                "selection_token": "local-parse-token",
                "save_path": r"C:\tmp\papers",
                "custom_save_path_confirmed": True,
                "selection_semantics": server.SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
                "parse_execution": "background",
                "papers": [
                    {
                        "index": 1,
                        "title": "Download Then Parse Paper",
                        "source": "arxiv",
                        "paper_id": "2606.00001",
                        "pdf_url": "https://example.org/paper.pdf",
                    }
                ],
            },
        )

        self.assertIn("Download selected", html)
        self.assertIn("/api/parse-selection/", html)
        self.assertIn("/api/parse-downloaded-selection/", html)
        self.assertIn('id="select-all"', html)
        self.assertIn('id="clear"', html)
        self.assertIn('id="skip-mineru"', html)
        self.assertNotIn('id="countdown-chip"', html)
        self.assertNotIn("MinerU optional", html)
        self.assertIn("/api/parse-prompt-timeout/", html)
        self.assertIn("/api/download-selection-timeout/", html)
        self.assertIn("selectionTimeoutTimer", html)
        self.assertIn("celebration-burst", html)
        self.assertIn("download-skeleton", html)
        self.assertIn("selection_timeout_seconds", html)
        self.assertIn("selection_expires_at", html)
        self.assertNotIn("progressFill", html)
        self.assertNotIn("STAGE_CLASS", html)


if __name__ == "__main__":
    unittest.main()
