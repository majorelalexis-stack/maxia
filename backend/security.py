"""MAXIA Art.1 V10.1 — Securite, filtrage contenu, rate limiting, garde-fous financiers"""
import re, time
from collections import defaultdict
from fastapi import HTTPException, Request
from config import (
    BLOCKED_WORDS, BLOCKED_PATTERNS,
    GROWTH_MAX_SPEND_DAY, GROWTH_MAX_SPEND_TX,
)

# ── Content filtering ──

_compiled_patterns = [re.compile(p) for p in BLOCKED_PATTERNS]

def check_content_safety(text: str, field_name: str = "content") -> None:
    """Verifie qu'un texte ne contient pas de contenu interdit."""
    lower = text.lower()
    for word in BLOCKED_WORDS:
        if word in lower:
            raise HTTPException(400, f"ART.1 — Contenu bloque dans {field_name}")
    for pattern in _compiled_patterns:
        if pattern.search(text):
            raise HTTPException(400, f"ART.1 — Contenu interdit detecte dans {field_name}")


# ── Rate limiting (in-memory, par IP) ──

_rate_store: dict = defaultdict(list)
RATE_LIMIT = 60
RATE_WINDOW = 60

def check_rate_limit(request: Request) -> None:
    """Rate limit simple par IP. 60 req/min."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if t > now - RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit depasse. Reessayez dans 1 minute.")
    _rate_store[ip].append(now)


# ── Garde-fous financiers (Art.4 V10.1) ──

_daily_spend_log: dict = {"date": "", "total": 0.0, "tx_count": 0}

def check_financial_limits(amount_usdc: float) -> dict:
    """
    Verifie les limites financieres avant une depense de l'agent.
    Retourne {"allowed": True/False, "reason": "..."}
    """
    today = time.strftime("%Y-%m-%d")

    # Reset journalier
    if _daily_spend_log["date"] != today:
        _daily_spend_log["date"] = today
        _daily_spend_log["total"] = 0.0
        _daily_spend_log["tx_count"] = 0

    # Limite par transaction
    if amount_usdc > GROWTH_MAX_SPEND_TX:
        return {
            "allowed": False,
            "reason": f"Montant {amount_usdc} USDC depasse la limite par tx ({GROWTH_MAX_SPEND_TX} USDC)",
        }

    # Limite journaliere
    if _daily_spend_log["total"] + amount_usdc > GROWTH_MAX_SPEND_DAY:
        return {
            "allowed": False,
            "reason": f"Budget journalier epuise ({_daily_spend_log['total']:.2f}/{GROWTH_MAX_SPEND_DAY} USDC)",
        }

    return {"allowed": True, "reason": "OK"}


def record_spend(amount_usdc: float):
    """Enregistre une depense dans le compteur journalier."""
    today = time.strftime("%Y-%m-%d")
    if _daily_spend_log["date"] != today:
        _daily_spend_log["date"] = today
        _daily_spend_log["total"] = 0.0
        _daily_spend_log["tx_count"] = 0
    _daily_spend_log["total"] += amount_usdc
    _daily_spend_log["tx_count"] += 1


def get_daily_spend_stats() -> dict:
    """Retourne les stats de depenses du jour."""
    return {
        "date": _daily_spend_log["date"],
        "total_usdc": _daily_spend_log["total"],
        "tx_count": _daily_spend_log["tx_count"],
        "limit_usdc": GROWTH_MAX_SPEND_DAY,
        "remaining_usdc": max(0, GROWTH_MAX_SPEND_DAY - _daily_spend_log["total"]),
    }
