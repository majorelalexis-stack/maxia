"""MAXIA Telegram bot — inline mode (Plan CEO V7 / P4B).

Inline mode lets users type ``@MAXIA_AI_bot <query>`` in any chat and get
instant results from MAXIA without opening the bot. Four patterns:

    price <TOKEN>           -> live price + 24h change + deep link
    swap <amt> <from> <to>  -> live quote + deep link
    gpu <tier>              -> available tiers + pricing
    agent <name>            -> service AI card + invoke link

All results are rendered as ``InlineQueryResultArticle`` with
``cache_time=5`` so Telegram refreshes prices every 5s — matching the
oracle staleness window.

This module is deliberately framework-free: it only builds dicts that
match the Telegram Bot API JSON shape. The caller wires it into the
existing httpx long-polling dispatcher.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger("maxia.telegram_inline")

# ── Tunables ──
MAX_QUERY_LENGTH: int = 256
MAX_RESULTS: int = 10
DEFAULT_CACHE_TIME: int = 5  # seconds — must match oracle HFT cache

# ── Content limits (Telegram API) ──
MAX_TITLE_LENGTH: int = 128
MAX_DESCRIPTION_LENGTH: int = 256
MAX_MESSAGE_TEXT_LENGTH: int = 4096

# ── Regex patterns (case-insensitive via re.I at match time) ──
_PRICE_RE = re.compile(r"^price\s+([A-Za-z0-9]{1,10})$", re.I)
_SWAP_RE = re.compile(
    r"^swap\s+(\d+(?:\.\d+)?)\s+([A-Za-z0-9]{1,10})\s+([A-Za-z0-9]{1,10})$",
    re.I,
)
_GPU_RE = re.compile(r"^gpu(?:\s+([A-Za-z0-9_\-]{1,20}))?$", re.I)
_AGENT_RE = re.compile(r"^agent\s+([A-Za-z0-9_\-]{1,40})$", re.I)

# ── Default deep-link base (can be overridden per install) ──
DEFAULT_BASE_URL: str = "https://maxiaworld.app"


class OracleProvider(Protocol):
    """Protocol for fetching live prices — injectable for tests."""

    async def get_price(self, symbol: str) -> dict:
        """Return dict with keys: price (float), source (str), change_24h (float|None)."""
        ...


class SwapProvider(Protocol):
    """Protocol for fetching swap quotes — injectable for tests."""

    async def get_quote(self, amount: float, from_sym: str, to_sym: str) -> dict:
        """Return dict with keys: out_amount (float), price_impact (float), source (str)."""
        ...


class GpuProvider(Protocol):
    """Protocol for GPU tier lookup — injectable for tests."""

    async def list_tiers(self, filter_name: Optional[str] = None) -> list[dict]:
        """Return list of {name, vram_gb, price_usd_hour, provider}."""
        ...


class AgentProvider(Protocol):
    """Protocol for agent/service lookup — injectable for tests."""

    async def find(self, name: str) -> list[dict]:
        """Return list of {name, description, price_usdc, id}."""
        ...


@dataclass(frozen=True)
class InlineHandlers:
    """Bundle of provider callables, immutable so it's safe to share."""
    oracle: OracleProvider
    swap: SwapProvider
    gpu: GpuProvider
    agent: AgentProvider
    base_url: str = DEFAULT_BASE_URL


# ── Result builders ──


def _truncate(text: str, limit: int) -> str:
    """Safe-truncate a string to ``limit`` characters."""
    if not isinstance(text, str):
        return ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _make_id(kind: str, *parts: object) -> str:
    """Deterministic unique ID for Telegram (required, max 64 bytes)."""
    bucket = int(time.time()) // DEFAULT_CACHE_TIME
    raw = f"{kind}|{bucket}|" + "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _article(
    *,
    result_id: str,
    title: str,
    description: str,
    message_text: str,
    parse_mode: str = "HTML",
    url: Optional[str] = None,
    thumb_url: Optional[str] = None,
) -> dict:
    """Build a Telegram InlineQueryResultArticle dict, with safe defaults."""
    article: dict = {
        "type": "article",
        "id": result_id[:64],
        "title": _truncate(title, MAX_TITLE_LENGTH),
        "description": _truncate(description, MAX_DESCRIPTION_LENGTH),
        "input_message_content": {
            "message_text": _truncate(message_text, MAX_MESSAGE_TEXT_LENGTH),
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
    }
    if url:
        article["url"] = url
        article["hide_url"] = False
    if thumb_url:
        article["thumbnail_url"] = thumb_url
    return article


def _format_price(value: float) -> str:
    """Human-friendly USD price string."""
    if value >= 1000:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:,.8f}".rstrip("0").rstrip(".")


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return ""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


# ── Individual handlers ──


async def handle_price(query: str, handlers: InlineHandlers) -> list[dict]:
    """Handle `price BTC` style inline queries."""
    m = _PRICE_RE.match(query)
    if not m:
        return []
    symbol = m.group(1).upper()
    try:
        data = await handlers.oracle.get_price(symbol)
    except Exception as e:
        logger.warning("[inline] oracle error for %s: %s", symbol, e)
        return []

    price = float(data.get("price") or 0.0)
    if price <= 0:
        return []

    change = data.get("change_24h")
    source = str(data.get("source") or "oracle")[:32]
    change_str = _format_pct(change) if change is not None else ""
    title = f"{symbol}: {_format_price(price)}"
    if change_str:
        title += f" ({change_str})"

    message_lines = [
        f"<b>{symbol}</b>: {_format_price(price)}",
    ]
    if change_str:
        message_lines.append(f"24h: {change_str}")
    message_lines.append(f"Source: {source}")
    message_lines.append(f"<a href=\"{handlers.base_url}/trade/{symbol}\">Trade on MAXIA</a>")

    return [_article(
        result_id=_make_id("price", symbol, price),
        title=title,
        description=f"Live price — {source}",
        message_text="\n".join(message_lines),
        url=f"{handlers.base_url}/trade/{symbol}",
    )]


async def handle_swap(query: str, handlers: InlineHandlers) -> list[dict]:
    """Handle `swap 100 USDC ETH` style inline queries."""
    m = _SWAP_RE.match(query)
    if not m:
        return []
    try:
        amount = float(m.group(1))
    except ValueError:
        return []
    if amount <= 0 or amount > 1_000_000:
        return []
    from_sym = m.group(2).upper()
    to_sym = m.group(3).upper()
    if from_sym == to_sym:
        return []

    try:
        data = await handlers.swap.get_quote(amount, from_sym, to_sym)
    except Exception as e:
        logger.warning("[inline] swap error %s->%s: %s", from_sym, to_sym, e)
        return []

    out_amount = float(data.get("out_amount") or 0.0)
    if out_amount <= 0:
        return []
    impact = data.get("price_impact")
    source = str(data.get("source") or "jupiter")[:32]

    title = f"Swap {amount} {from_sym} -> {out_amount:,.6g} {to_sym}"
    lines = [
        f"<b>{amount} {from_sym}</b> -> <b>{out_amount:,.6g} {to_sym}</b>",
        f"Source: {source}",
    ]
    if impact is not None:
        lines.append(f"Price impact: {float(impact):.2f}%")
    cta = f"{handlers.base_url}/swap?from={from_sym}&to={to_sym}&amount={amount}"
    lines.append(f"<a href=\"{cta}\">Execute on MAXIA</a>")

    return [_article(
        result_id=_make_id("swap", amount, from_sym, to_sym, out_amount),
        title=title,
        description=f"Quote via {source}",
        message_text="\n".join(lines),
        url=cta,
    )]


async def handle_gpu(query: str, handlers: InlineHandlers) -> list[dict]:
    """Handle `gpu` or `gpu rtx4090` style inline queries."""
    m = _GPU_RE.match(query)
    if not m:
        return []
    filter_name = (m.group(1) or "").strip().lower() or None

    try:
        tiers = await handlers.gpu.list_tiers(filter_name)
    except Exception as e:
        logger.warning("[inline] gpu error: %s", e)
        return []

    if not tiers:
        return []

    results: list[dict] = []
    for tier in tiers[:MAX_RESULTS]:
        name = str(tier.get("name", "?"))[:20]
        vram = int(tier.get("vram_gb") or 0)
        price_h = float(tier.get("price_usd_hour") or 0.0)
        provider = str(tier.get("provider", "akash"))[:20]
        if price_h <= 0 or not name:
            continue

        title = f"GPU {name} — ${price_h:.3f}/h"
        description = f"{vram}GB VRAM via {provider}"
        cta = f"{handlers.base_url}/gpu/rent?tier={name}"
        lines = [
            f"<b>GPU {name}</b>",
            f"VRAM: {vram}GB",
            f"Price: ${price_h:.3f}/hour",
            f"Provider: {provider}",
            f"<a href=\"{cta}\">Rent on MAXIA</a>",
        ]
        results.append(_article(
            result_id=_make_id("gpu", name, price_h),
            title=title,
            description=description,
            message_text="\n".join(lines),
            url=cta,
        ))
    return results


async def handle_agent(query: str, handlers: InlineHandlers) -> list[dict]:
    """Handle `agent CEO` style inline queries."""
    m = _AGENT_RE.match(query)
    if not m:
        return []
    name = m.group(1)

    try:
        services = await handlers.agent.find(name)
    except Exception as e:
        logger.warning("[inline] agent error for %s: %s", name, e)
        return []

    if not services:
        return []

    results: list[dict] = []
    for svc in services[:MAX_RESULTS]:
        svc_id = str(svc.get("id") or svc.get("name", "?"))[:40]
        svc_name = str(svc.get("name", svc_id))[:40]
        price_usdc = float(svc.get("price_usdc") or 0.0)
        description_raw = str(svc.get("description", ""))[:200]

        title = f"{svc_name} — {price_usdc:.2f} USDC"
        cta = f"{handlers.base_url}/agent/{svc_id}"
        lines = [
            f"<b>{svc_name}</b>",
            f"Price: {price_usdc:.2f} USDC",
            description_raw,
            f"<a href=\"{cta}\">Invoke on MAXIA</a>",
        ]
        results.append(_article(
            result_id=_make_id("agent", svc_id),
            title=title,
            description=description_raw or "AI agent service",
            message_text="\n".join([line for line in lines if line]),
            url=cta,
        ))
    return results


# ── Main dispatcher ──


async def route_inline_query(
    query_text: str, handlers: InlineHandlers
) -> list[dict]:
    """Route an inline query to the appropriate handler and return results.

    Returns an empty list if the query is malformed, too long, or no
    handler matched. Caller should ``answerInlineQuery`` with the list
    (Telegram accepts empty arrays).
    """
    if not isinstance(query_text, str):
        return []
    query = query_text.strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        return []

    # Dispatch by first keyword (case-insensitive)
    keyword = query.split(None, 1)[0].lower()

    if keyword == "price":
        return await handle_price(query, handlers)
    if keyword == "swap":
        return await handle_swap(query, handlers)
    if keyword == "gpu":
        return await handle_gpu(query, handlers)
    if keyword == "agent":
        return await handle_agent(query, handlers)

    return []


def build_answer_payload(
    inline_query_id: str,
    results: list[dict],
    *,
    cache_time: int = DEFAULT_CACHE_TIME,
    is_personal: bool = False,
) -> dict:
    """Build the JSON body for Telegram's ``answerInlineQuery`` call."""
    return {
        "inline_query_id": str(inline_query_id)[:64],
        "results": results[:MAX_RESULTS],
        "cache_time": max(0, min(86400, int(cache_time))),
        "is_personal": bool(is_personal),
    }
