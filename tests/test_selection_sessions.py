import asyncio
import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, patch

from paper_search_mcp import cache, server


class TestSelectionSessions(unittest.TestCase):
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
            self.assertEqual(result["papers"][0]["reason"], "direct_pdf_url")

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
            {"PAPER_SEARCH_MCP_CACHE_DIR": tmp},
        ), patch(
            "paper_search_mcp.server.search_papers",
            new=AsyncMock(return_value=fake_search_result),
        ):
            result = asyncio.run(
                server.crawl_papers_for_selection(
                    "agent skill",
                    max_results_per_source=11,
                    sources="arxiv",
                )
            )
            loaded = cache.get_search_session(result["selection_token"], cache_dir=tmp)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total"], 11)
        self.assertEqual(len(result["papers"]), 11)
        self.assertEqual(len(result["numbered_fallback"]), 11)
        self.assertEqual(result["app"]["render_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(result["app"]["selection_token"], result["selection_token"])
        self.assertEqual(len(loaded["papers"]), 11)
        self.assertEqual(loaded["metadata"]["interaction"], "crawl_papers_for_selection")

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

    def test_download_selected_papers_returns_parse_prompt_without_parsing(self):
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
                "paper_search_mcp.server.parse_pdf_with_mineru",
                new=AsyncMock(side_effect=AssertionError("download_selected_papers must not parse")),
            ) as parse_mock:
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                    )
                )
                manifest_exists = Path(result["manifest_path"]).exists()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(result["results"][0]["status"], "downloaded")
        self.assertNotIn("parse", result["results"][0])
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(result["parse_prompt"]["recommended_selected_indices"], "all")
        self.assertEqual(result["parse_prompt"]["parse_ready_total"], 1)
        self.assertTrue(manifest_exists)
        parse_mock.assert_not_awaited()

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
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                    )
                )

        expected_root = Path(tmp) / "Desktop" / "papers"
        self.assertEqual(Path(result["save_path"]), expected_root.resolve())
        self.assertEqual(Path(result["results"][0]["pdf_path"]).parent, expected_root.resolve())
        self.assertEqual(Path(result["results"][0]["pdf_path"]).name, "2601.12345v1.pdf")

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
                "paper_search_mcp.server.parse_pdf_with_mineru",
                new=AsyncMock(side_effect=AssertionError("download_selected_papers must not parse")),
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="all",
                        save_path=tmp,
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
        self.assertEqual(result["parse_prompt"]["recommended_tool"], server.PAPER_SELECTION_WIDGET_TOOL)
        self.assertEqual(result["parse_prompt"]["total"], server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)
        self.assertEqual(result["app"]["render_tool"], server.PAPER_SELECTION_WIDGET_TOOL)

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
            ):
                result = asyncio.run(
                    server.download_selected_papers(
                        session["selection_token"],
                        selected_indices="1",
                        save_path=tmp,
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["results"][0]["status"], "skipped_existing")
        self.assertTrue(result["results"][0]["valid_pdf"])

    def test_crawl_download_parse_papers_searches_downloads_and_returns_prompt(self):
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
                "paper_search_mcp.server.search_papers",
                new=AsyncMock(return_value=fake_search_result),
            ) as search_mock, patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ):
                result = asyncio.run(
                    server.crawl_download_parse_papers(
                        "agent skill",
                        count=2,
                        ranking_profile="agent-skill",
                        save_path=tmp,
                    )
                )
                downloaded_names = sorted(path.name for path in Path(tmp).glob("*.pdf"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["download"]["downloaded"], 2)
        self.assertEqual(result["parse_prompt"]["recommended_tool"], "submit_parse_job")
        self.assertEqual(downloaded_names[0], "2601.10001v1.pdf")
        self.assertEqual(search_mock.await_args.kwargs["sources"], "agent-skill-fast")

    def test_paper_research_workflow_downloads_and_submits_parse_job(self):
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
                "paper_search_mcp.server.search_papers",
                new=AsyncMock(return_value=fake_search_result),
            ), patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(side_effect=fake_download),
            ), patch(
                "paper_search_mcp.server.submit_parse_job",
                new=AsyncMock(return_value={"status": "submitted", "job_id": "parse-test"}),
            ) as submit_mock:
                result = asyncio.run(
                    server.paper_research_workflow(
                        "agent skill",
                        count=1,
                        ranking_profile="agent-skill",
                        save_path=tmp,
                    )
                )

        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["workflow"]["mcp_first"])
        self.assertEqual(result["workflow"]["next_tool"], "get_parse_job_status")
        self.assertEqual(result["parse_job"]["job_id"], "parse-test")
        submit_mock.assert_awaited_once()
        self.assertEqual(submit_mock.await_args.kwargs["selected_indices"], "all")

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
        self.assertEqual(result["workflow"]["next_tool"], "download_selected_papers")
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
                    )
                )

            self.assertEqual(result["status"], "invalid_save_path")
            self.assertEqual(result["allow_env"], "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH")

    def test_after_saved_pdf_auto_parses_without_context_when_under_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "saved.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            parse_payload = {
                "status": "ok",
                "results": [{"status": "ok"}],
                "total": 1,
                "parsed": 1,
                "failed": 0,
                "skipped": 0,
            }

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(return_value=parse_payload),
            ) as parse_mock:
                result = asyncio.run(
                    server._after_saved_pdf(
                        str(pdf),
                        source="arxiv",
                        paper_id="2601.00005",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                    )
                )

            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(result["pdf_path"], str(pdf.resolve()))
            prompt = result["parse_prompt"]
            self.assertEqual(prompt["status"], "ok")
            self.assertEqual(prompt["interaction"], "auto_parse_saved_pdfs")
            self.assertEqual(prompt["selected_indices"], [1])
            self.assertEqual(prompt["auto_parse_limit"], server.AUTO_PARSE_SAVED_PDF_LIMIT)
            self.assertNotIn("app", result)
            parse_mock.assert_awaited_once()
            self.assertEqual(parse_mock.await_args.kwargs["selected_indices"], "1")

            loaded = cache.get_search_session(prompt["selection_token"], cache_dir=tmp)
            self.assertEqual(loaded["metadata"]["trigger"], "pdf_saved")
            self.assertEqual(loaded["papers"][0]["local_pdf_path"], str(pdf.resolve()))

    def test_after_saved_pdfs_at_limit_auto_parses_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdfs = []
            for index in range(server.AUTO_PARSE_SAVED_PDF_LIMIT):
                pdf = Path(tmp) / f"saved-{index + 1}.pdf"
                pdf.write_bytes(b"%PDF-1.4\n%%EOF")
                pdfs.append(str(pdf))
            parse_payload = {
                "status": "ok",
                "results": [{"status": "ok"} for _ in pdfs],
                "total": len(pdfs),
                "parsed": len(pdfs),
                "failed": 0,
                "skipped": 0,
            }

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(return_value=parse_payload),
            ) as parse_mock:
                result = asyncio.run(
                    server._after_saved_pdfs(
                        pdfs,
                        source="arxiv",
                        paper_id="batch",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=None,
                    )
                )

            prompt = result["parse_prompt"]
            self.assertEqual(prompt["status"], "ok")
            self.assertEqual(prompt["interaction"], "auto_parse_saved_pdfs")
            self.assertEqual(prompt["selected_indices"], list(range(1, server.AUTO_PARSE_SAVED_PDF_LIMIT + 1)))
            parse_mock.assert_awaited_once()
            self.assertEqual(
                parse_mock.await_args.kwargs["selected_indices"],
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

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(side_effect=AssertionError("large batches should not auto-parse")),
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

    def test_after_saved_pdfs_over_limit_elicitation_accepts_and_parses_selection(self):
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
            parse_payload = {
                "status": "ok",
                "results": [{"status": "ok"}],
                "total": 1,
                "parsed": 1,
                "failed": 0,
                "skipped": 0,
            }
            ctx = FakeContext()

            with patch.dict(os.environ, {"PAPER_SEARCH_MCP_CACHE_DIR": tmp}), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(return_value=parse_payload),
            ) as parse_mock:
                result = asyncio.run(
                    server._after_saved_pdfs(
                        pdfs,
                        source="arxiv",
                        paper_id="2601.00006",
                        title="Saved PDF",
                        save_path=tmp,
                        downloader="test.download_pdf",
                        ctx=ctx,
                    )
                )

            self.assertEqual(result["parse_prompt"]["interaction"], "elicitation")
            self.assertEqual(result["parse_prompt"]["selected_indices"], [1])
            parse_mock.assert_awaited_once()
            self.assertEqual(parse_mock.await_args.kwargs["selected_indices"], "1")
            schema_items = ctx.schema.model_json_schema()["properties"]["selected_papers"]["items"]
            self.assertIn("1. Saved PDF", schema_items["enum"][0])

    def test_read_source_paper_detects_saved_pdf_and_prompts(self):
        class FakeSearcher:
            def read_paper(self, paper_id, save_path):
                Path(save_path).mkdir(parents=True, exist_ok=True)
                (Path(save_path) / "read-saved.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
                return "extracted text"

        with tempfile.TemporaryDirectory() as tmp:
            parse_payload = {
                "status": "ok",
                "results": [{"status": "ok"}],
                "total": 1,
                "parsed": 1,
                "failed": 0,
                "skipped": 0,
            }
            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH": "true",
                    "PAPER_SEARCH_MCP_CACHE_DIR": tmp,
                },
            ), patch(
                "paper_search_mcp.server.parse_selected_papers",
                new=AsyncMock(return_value=parse_payload),
            ):
                result = asyncio.run(
                    server._read_source_paper(
                        FakeSearcher(),
                        source="arxiv",
                        paper_id="2601.00007",
                        save_path=tmp,
                        ctx=None,
                    )
                )

            self.assertEqual(result["status"], "read")
            self.assertEqual(result["text"], "extracted text")
            prompt = result["saved_pdf_prompt"]["parse_prompt"]
            self.assertEqual(prompt["status"], "ok")
            self.assertEqual(prompt["interaction"], "auto_parse_saved_pdfs")
            self.assertEqual(prompt["selected_indices"], [1])

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

        self.assertEqual(result["interaction"], "mcp_app")
        self.assertEqual(result["selection_token"], session["selection_token"])
        self.assertEqual(result["papers"][0]["title"], "Widget Paper")
        self.assertEqual(result["papers"][0]["parse_ready"], True)
        self.assertEqual(result["papers"][0]["published_date"], "2026-01-08")
        self.assertEqual(result["papers"][0]["publication_venue"], "Computer Vision and Pattern Recognition")
        self.assertEqual(result["papers"][0]["original_url"], "https://example.org/widget")
        self.assertEqual(result["_meta"]["output_template"], server.PAPER_SELECTION_WIDGET_URI)
        self.assertEqual(result["mode"], "pypdf")

    def test_paper_selection_widget_contains_checkbox_and_tool_call(self):
        html = asyncio.run(server.paper_selection_widget())
        self.assertIn('type="checkbox"', html)
        self.assertIn("submit_parse_job", html)
        self.assertIn("window.openai?.callTool", html)
        self.assertIn("unwrapToolOutput", html)
        self.assertIn("value?.result", html)

    def test_open_paper_selection_page_serves_checkbox_page_and_posts_selection(self):
        async def fake_submit_parse_job(**kwargs):
            return {
                "status": "submitted",
                "job_id": "parse-test",
                "selection_token": kwargs["selection_token"],
                "selected_indices": kwargs["selected_indices"],
            }

        with patch("paper_search_mcp.server.webbrowser.open", return_value=True), patch(
            "paper_search_mcp.server.submit_parse_job",
            new=AsyncMock(side_effect=fake_submit_parse_job),
        ):
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

            request = urllib.request.Request(
                result["url"].replace("/paper-selection/", "/api/parse-selection/"),
                data=json.dumps({"selected_indices": "1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            body = json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))

        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["job_id"], "parse-test")
        self.assertEqual(body["selection_token"], "local-test-token")
        self.assertEqual(body["selected_indices"], "1")


if __name__ == "__main__":
    unittest.main()
