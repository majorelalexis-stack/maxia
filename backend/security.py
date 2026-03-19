"""MAXIA Art.1 V12 — Securite, filtrage contenu, rate limiting, garde-fous financiers"""
import re, time, json
from collections import defaultdict
from pathlib import Path
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


# ── Rate limiting (in-memory, par IP) avec nettoyage automatique ──

_rate_store: dict = defaultdict(list)
RATE_LIMIT = 60
RATE_WINDOW = 60
_RATE_STORE_MAX_KEYS = 10000


def _cleanup_rate_store():
    """Evite la fuite memoire en nettoyant les entrees expirees."""
    now = time.time()
    expired_keys = [ip for ip, ts in _rate_store.items() if not ts or ts[-1] < now - RATE_WINDOW * 2]
    for ip in expired_keys:
        del _rate_store[ip]


def check_rate_limit(request: Request) -> None:
    """Rate limit simple par IP. 60 req/min."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if t > now - RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit depasse. Reessayez dans 1 minute.")
    _rate_store[ip].append(now)
    # Nettoyage periodique pour eviter fuite memoire
    if len(_rate_store) > _RATE_STORE_MAX_KEYS:
        _cleanup_rate_store()


# ── Garde-fous financiers (Art.4 V12) avec persistance fichier ──

_SPEND_FILE = Path(__file__).parent / ".daily_spend.json"


def _load_spend_log() -> dict:
    """Charge le log de depenses depuis le fichier."""
    try:
        if _SPEND_FILE.exists():
            data = json.loads(_SPEND_FILE.read_text())
            if data.get("date") == time.strftime("%Y-%m-%d"):
                return data
    except Exception:
        pass
    return {"date": time.strftime("%Y-%m-%d"), "total": 0.0, "tx_count": 0}


def _save_spend_log(log: dict):
    """Sauvegarde le log de depenses sur disque."""
    try:
        _SPEND_FILE.write_text(json.dumps(log))
    except Exception as e:
        print(f"[Security] Erreur sauvegarde spend log: {e}")


def check_financial_limits(amount_usdc: float) -> dict:
    """
    Verifie les limites financieres avant une depense de l'agent.
    Retourne {"allowed": True/False, "reason": "..."}
    """
    log = _load_spend_log()

    # Limite par transaction
    if amount_usdc > GROWTH_MAX_SPEND_TX:
        return {
            "allowed": False,
            "reason": f"Montant {amount_usdc} USDC depasse la limite par tx ({GROWTH_MAX_SPEND_TX} USDC)",
        }

    # Limite journaliere
    if log["total"] + amount_usdc > GROWTH_MAX_SPEND_DAY:
        return {
            "allowed": False,
            "reason": f"Budget journalier epuise ({log['total']:.2f}/{GROWTH_MAX_SPEND_DAY} USDC)",
        }

    return {"allowed": True, "reason": "OK"}


def record_spend(amount_usdc: float):
    """Enregistre une depense dans le compteur journalier (persiste sur disque)."""
    log = _load_spend_log()
    log["total"] += amount_usdc
    log["tx_count"] += 1
    _save_spend_log(log)


def get_daily_spend_stats() -> dict:
    """Retourne les stats de depenses du jour."""
    log = _load_spend_log()
    return {
        "date": log["date"],
        "total_usdc": log["total"],
        "tx_count": log["tx_count"],
        "limit_usdc": GROWTH_MAX_SPEND_DAY,
        "remaining_usdc": max(0, GROWTH_MAX_SPEND_DAY - log["total"]),
    }
