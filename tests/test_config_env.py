import os
import tempfile
import unittest
from unittest.mock import patch

from paper_search_mcp import config


class TestConfigEnv(unittest.TestCase):
    def test_prefixed_env_has_priority_over_legacy(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_CORE_API_KEY": "prefixed-value",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", ""), "prefixed-value")

    def test_legacy_env_fallback_still_works(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", ""), "legacy-value")

    def test_empty_prefixed_value_blocks_legacy_fallback(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_CORE_API_KEY": "",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", "default"), "")

    def test_loads_from_custom_env_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = os.path.join(tmp_dir, "paper-search.env")
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                tmp.write("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=test@example.com\n")

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": tmp_path,
                },
                clear=True,
            ):
                config.load_env_file(force=True)
                self.assertEqual(config.get_env("UNPAYWALL_EMAIL", ""), "test@example.com")

    def test_set_env_value_updates_existing_prefixed_key(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = os.path.join(tmp_dir, "paper-search.env")
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                tmp.write("PAPER_SEARCH_MCP_MINERU_API_KEY=old\nOTHER=value\n")

            with patch.dict(os.environ, {}, clear=True):
                written = config.set_env_value("MINERU_API_KEY", "new-token", env_file=config.Path(tmp_path))

            self.assertEqual(str(written), tmp_path)
            with open(tmp_path, encoding="utf-8") as saved:
                content = saved.read()
            self.assertIn("PAPER_SEARCH_MCP_MINERU_API_KEY=new-token\n", content)
            self.assertIn("OTHER=value\n", content)
            self.assertNotIn("old", content)


if __name__ == "__main__":
    unittest.main()
