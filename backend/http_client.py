"""Shared HTTP client singleton — avoids creating new httpx.AsyncClient per request."""
import httpx
import logging

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared async HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            follow_redirects=True,
        )
    return _client


async def close_http_client():
    """Close the shared client. Call during shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
    logger.info("[HTTP] Shared client closed")
