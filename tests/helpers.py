"""Shared test helpers for paper-search-mcp tests.

Contains common utility functions used across platform-specific test files
to reduce duplication.
"""

import requests


def check_api_accessible(url: str, timeout: int = 10) -> bool:
    """Check whether an API endpoint is reachable.

    Used by @unittest.skipUnless decorators and setUpClass methods
    to conditionally skip network-dependent tests.

    Args:
        url: The API endpoint URL to check.
        timeout: Request timeout in seconds.

    Returns:
        True if the endpoint responded with HTTP 200, False otherwise.
    """
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False
