"""Tests for API-key-gated skeleton connectors (ACM & IEEE)."""
import os
import unittest
import unittest.mock


class _SkeletonConnectorTest:
    """Shared tests for API-key skeleton connectors (ACM Digital Library, IEEE Xplore)."""

    module_name = ""    # e.g. "paper_search_mcp.academic_platforms.acm"
    searcher_name = ""  # e.g. "ACMSearcher"
    key_env = ""        # e.g. "ACM_API_KEY"
    prefixed_key_env = ""  # e.g. "PAPER_SEARCH_MCP_ACM_API_KEY"
    source_id = ""      # e.g. "acm"

    def setUp(self):
        self._original = os.environ.pop(self.key_env, None)

    def tearDown(self):
        if self._original is not None:
            os.environ[self.key_env] = self._original
        else:
            os.environ.pop(self.key_env, None)

    def _make_searcher(self):
        import importlib
        mod = importlib.import_module(self.module_name)
        return getattr(mod, self.searcher_name)()

    def test_is_not_configured_without_key(self):
        searcher = self._make_searcher()
        self.assertFalse(searcher.is_configured())

    def test_search_raises_not_implemented_without_key(self):
        searcher = self._make_searcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.search("test query")
        self.assertIn(self.key_env, str(ctx.exception))

    def test_download_raises_not_implemented_without_key(self):
        searcher = self._make_searcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.download_pdf("test_id")
        self.assertIn(self.key_env, str(ctx.exception))

    def test_read_raises_not_implemented_without_key(self):
        searcher = self._make_searcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.read_paper("test_id")
        self.assertIn(self.key_env, str(ctx.exception))

    def test_not_in_all_sources_without_key(self):
        import importlib
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertNotIn(self.source_id, srv_module.ALL_SOURCES)


class _SkeletonConfiguredTest:
    """Shared tests verifying behaviour when API key IS present."""

    module_name = ""    # set by subclasses
    searcher_name = ""  # set by subclasses
    prefixed_key_env = ""  # set by subclasses

    def _make_searcher(self):
        import importlib
        mod = importlib.import_module(self.module_name)
        return getattr(mod, self.searcher_name)()

    def test_is_configured_with_key(self):
        with unittest.mock.patch.dict(os.environ, {self.prefixed_key_env: "dummy_test_key"}):
            searcher = self._make_searcher()
            self.assertTrue(searcher.is_configured())

    def test_search_raises_not_implemented_even_with_key(self):
        with unittest.mock.patch.dict(os.environ, {self.prefixed_key_env: "dummy_test_key"}):
            searcher = self._make_searcher()
            with self.assertRaises(NotImplementedError) as ctx:
                searcher.search("test query")
            self.assertNotIn(self.key_env, str(ctx.exception))


class TestACMDisabledByDefault(unittest.TestCase, _SkeletonConnectorTest):
    module_name = "paper_search_mcp.academic_platforms.acm"
    searcher_name = "ACMSearcher"
    key_env = "ACM_API_KEY"
    prefixed_key_env = "PAPER_SEARCH_MCP_ACM_API_KEY"
    source_id = "acm"


class TestACMIsConfiguredWithKey(unittest.TestCase, _SkeletonConfiguredTest):
    module_name = "paper_search_mcp.academic_platforms.acm"
    searcher_name = "ACMSearcher"
    key_env = "ACM_API_KEY"
    prefixed_key_env = "PAPER_SEARCH_MCP_ACM_API_KEY"


class TestIEEEDisabledByDefault(unittest.TestCase, _SkeletonConnectorTest):
    module_name = "paper_search_mcp.academic_platforms.ieee"
    searcher_name = "IEEESearcher"
    key_env = "IEEE_API_KEY"
    prefixed_key_env = "PAPER_SEARCH_MCP_IEEE_API_KEY"
    source_id = "ieee"


class TestIEEEIsConfiguredWithKey(unittest.TestCase, _SkeletonConfiguredTest):
    module_name = "paper_search_mcp.academic_platforms.ieee"
    searcher_name = "IEEESearcher"
    key_env = "IEEE_API_KEY"
    prefixed_key_env = "PAPER_SEARCH_MCP_IEEE_API_KEY"


if __name__ == "__main__":
    unittest.main()
