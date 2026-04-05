"""MAXIA API client — sync HTTP layer for AutoGen tools."""

from __future__ import annotations

import os
from typing import Any

import httpx

_BASE_URL = os.getenv("MAXIA_API_URL", "https://maxiaworld.app")
_TIMEOUT = 20.0


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    key = os.getenv("MAXIA_API_KEY", "")
    if key:
        h["X-API-Key"] = key
    return h


def maxia_get(path: str, params: dict[str, Any] | None = None) -> dict:
    """GET request to MAXIA API."""
    resp = httpx.get(
        f"{_BASE_URL}{path}",
        params=params,
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def maxia_post(path: str, body: dict[str, Any]) -> dict:
    """POST request to MAXIA API."""
    resp = httpx.post(
        f"{_BASE_URL}{path}",
        json=body,
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
