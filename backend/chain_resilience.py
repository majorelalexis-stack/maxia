"""MAXIA — Chain Resilience: Circuit breaker + failover pour les 14 blockchains.

Chaque chain dispose de son propre circuit breaker avec 3 etats:
  CLOSED  = fonctionnement normal, les appels passent
  OPEN    = chain en panne, appels bloques (failover automatique)
  HALF_OPEN = test de recuperation, laisse passer quelques appels

Thread-safe via asyncio.Lock. Aucune dependance externe (stdlib + httpx).
"""

import asyncio
import logging
import time
from typing import Any, Optional

import os
from fastapi import APIRouter, Request

from config import (
    SOLANA_RPC, BASE_RPC, ETH_RPC, XRPL_RPC,
    TON_API_URL, SUI_RPC, POLYGON_RPC, ARBITRUM_RPC,
    AVALANCHE_RPC, BNB_RPC, TRON_API_URL, NEAR_RPC,
    APTOS_API, SEI_RPC,
)

logger = logging.getLogger("maxia.chain_resilience")

# ── Etats du circuit breaker ──
STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

# ── Configuration par defaut ──
DEFAULT_FAIL_MAX = 3          # Echecs avant ouverture du circuit
DEFAULT_RESET_TIMEOUT = 30.0  # Secondes avant tentative half-open
DEFAULT_SUCCESS_TO_CLOSE = 2  # Succes en half-open pour refermer

# ── Retry avec backoff exponentiel ──
MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # 1s, 2s, 4s


# ── Les 14 chains supportees avec leurs RPC par defaut ──
CHAIN_RPC_MAP: dict[str, str] = {
    "solana": SOLANA_RPC,
    "base": BASE_RPC,
    "ethereum": ETH_RPC,
    "xrpl": XRPL_RPC,
    "ton": TON_API_URL,
    "sui": SUI_RPC,
    "polygon": POLYGON_RPC,
    "arbitrum": ARBITRUM_RPC,
    "avalanche": AVALANCHE_RPC,
    "bnb": BNB_RPC,
    "tron": TRON_API_URL,
    "near": NEAR_RPC,
    "aptos": APTOS_API,
    "sei": SEI_RPC,
}

# ── Multi-RPC providers par chain (primary + 2 fallback publics) ──
# Chaque chain a 2-3 endpoints: le primaire (.env) + des publics rate-limited.
# L'auto-failover teste chaque provider dans l'ordre avant de passer a une autre chain.
CHAIN_RPC_PROVIDERS: dict[str, list[str]] = {
    "solana": [
        SOLANA_RPC,
        "https://api.mainnet-beta.solana.com",
        "https://rpc.ankr.com/solana",
    ],
    "base": [
        BASE_RPC,
        "https://mainnet.base.org",
        "https://rpc.ankr.com/base",
    ],
    "ethereum": [
        ETH_RPC,
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
    ],
    "xrpl": [
        XRPL_RPC,
        "https://s2.ripple.com:51234/",
        "https://xrplcluster.com/",
    ],
    "ton": [
        TON_API_URL,
        "https://toncenter.com/api/v2",
    ],
    "sui": [
        SUI_RPC,
        "https://fullnode.mainnet.sui.io:443",
        "https://rpc.ankr.com/sui",
    ],
    "polygon": [
        POLYGON_RPC,
        "https://polygon-rpc.com",
        "https://rpc.ankr.com/polygon",
    ],
    "arbitrum": [
        ARBITRUM_RPC,
        "https://arb1.arbitrum.io/rpc",
        "https://rpc.ankr.com/arbitrum",
    ],
    "avalanche": [
        AVALANCHE_RPC,
        "https://api.avax.network/ext/bc/C/rpc",
        "https://rpc.ankr.com/avalanche",
    ],
    "bnb": [
        BNB_RPC,
        "https://bsc-dataseed.binance.org",
        "https://rpc.ankr.com/bsc",
    ],
    "tron": [
        TRON_API_URL,
        "https://api.trongrid.io",
    ],
    "near": [
        NEAR_RPC,
        "https://rpc.mainnet.near.org",
        "https://near.lava.build",
    ],
    "aptos": [
        APTOS_API,
        "https://fullnode.mainnet.aptoslabs.com/v1",
    ],
    "sei": [
        SEI_RPC,
        "https://evm-rpc.sei-apis.com",
        "https://rpc.ankr.com/sei",
    ],
}
# Deduplicate providers per chain
for _chain, _urls in CHAIN_RPC_PROVIDERS.items():
    seen: set[str] = set()
    deduped: list[str] = []
    for u in _urls:
        if u and u not in seen:
            seen.add(u)
            deduped.append(u)
    CHAIN_RPC_PROVIDERS[_chain] = deduped

# ── Timeout par type de chain (ms) ──
# EVM chains sont rapides (<2s), non-EVM peuvent etre plus lents.
CHAIN_TIMEOUT_MS: dict[str, int] = {
    "solana": 5000,
    "base": 3000,
    "ethereum": 5000,
    "xrpl": 5000,
    "ton": 8000,
    "sui": 5000,
    "polygon": 3000,
    "arbitrum": 3000,
    "avalanche": 3000,
    "bnb": 3000,
    "tron": 8000,
    "near": 5000,
    "aptos": 5000,
    "sei": 3000,
}

# ── Sante par RPC provider (latence + echecs) ──
_rpc_health: dict[str, dict] = {}  # {url: {"latencies": [], "failures": int, "last_fail": float}}

def get_best_rpc(chain: str) -> str:
    """Retourne le meilleur RPC disponible pour une chain (par latence + sante)."""
    providers = CHAIN_RPC_PROVIDERS.get(chain.lower(), [])
    if not providers:
        return CHAIN_RPC_MAP.get(chain.lower(), "")

    best_url = providers[0]
    best_score = float("inf")

    for url in providers:
        health = _rpc_health.get(url)
        if not health:
            # Pas encore teste — priorite au premier dans la liste
            return url
        failures = health.get("failures", 0)
        if failures >= DEFAULT_FAIL_MAX:
            # Provider en panne — skip
            last_fail = health.get("last_fail", 0)
            if time.monotonic() - last_fail < DEFAULT_RESET_TIMEOUT:
                continue
        latencies = health.get("latencies", [])
        avg_lat = sum(latencies) / len(latencies) if latencies else 1.0
        score = avg_lat + (failures * 0.5)
        if score < best_score:
            best_score = score
            best_url = url

    return best_url


def record_rpc_result(url: str, latency: float, success: bool):
    """Enregistre le resultat d'un appel RPC pour le scoring des providers."""
    if url not in _rpc_health:
        _rpc_health[url] = {"latencies": [], "failures": 0, "last_fail": 0.0}
    h = _rpc_health[url]
    h["latencies"].append(latency)
    if len(h["latencies"]) > 20:
        h["latencies"].pop(0)
    if success:
        h["failures"] = max(0, h["failures"] - 1)
    else:
        h["failures"] += 1
        h["last_fail"] = time.monotonic()


# ── 7 chains avec swap natif (Jupiter + 6 EVM via 0x) ──
SWAP_CHAINS = ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb"]

# ── Fallback chains par categorie (EVM compatibles entre eux, etc.) ──
CHAIN_FALLBACKS: dict[str, list[str]] = {
    "solana": [],
    "base": ["arbitrum", "polygon", "ethereum"],
    "ethereum": ["base", "arbitrum", "polygon"],
    "xrpl": [],
    "ton": [],
    "sui": [],
    "polygon": ["arbitrum", "base", "avalanche"],
    "arbitrum": ["base", "polygon", "ethereum"],
    "avalanche": ["polygon", "bnb", "arbitrum"],
    "bnb": ["polygon", "avalanche", "arbitrum"],
    "tron": [],
    "near": [],
    "aptos": [],
    "sei": ["polygon", "arbitrum", "base"],
}


class ChainCircuitBreaker:
    """
    Circuit breaker pour une blockchain individuelle.

    Flux:
      CLOSED -> (fail_max echecs) -> OPEN
      OPEN -> (apres reset_timeout) -> HALF_OPEN
      HALF_OPEN -> (success_to_close succes) -> CLOSED
      HALF_OPEN -> (1 echec) -> OPEN
    """

    def __init__(
        self,
        chain_name: str,
        fail_max: int = DEFAULT_FAIL_MAX,
        reset_timeout: float = DEFAULT_RESET_TIMEOUT,
        success_to_close: int = DEFAULT_SUCCESS_TO_CLOSE,
    ):
        self.chain_name = chain_name
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.success_to_close = success_to_close

        # Compteurs internes
        self._failures: int = 0
        self._successes_in_half_open: int = 0
        self._state: str = STATE_CLOSED
        self._last_failure_time: float = 0.0
        self._last_failure_error: Optional[str] = None
        self._total_calls: int = 0
        self._total_failures: int = 0

        # Latence (moyenne glissante sur les 50 derniers appels)
        self._latencies: list[float] = []
        self._max_latency_samples = 50

        # Verrou asyncio pour thread-safety
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Etat courant du circuit breaker (avec transition auto OPEN -> HALF_OPEN)."""
        if self._state == STATE_OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.reset_timeout:
                return STATE_HALF_OPEN
        return self._state

    @property
    def stats(self) -> dict:
        """Statistiques du circuit breaker."""
        avg_ms = 0.0
        if self._latencies:
            avg_ms = round(sum(self._latencies) / len(self._latencies) * 1000, 1)

        return {
            "chain": self.chain_name,
            "state": self.state,
            "failures": self._failures,
            "total_failures": self._total_failures,
            "total_calls": self._total_calls,
            "successes_in_half_open": self._successes_in_half_open,
            "last_failure": self._last_failure_error,
            "last_failure_time": self._last_failure_time if self._last_failure_time > 0 else None,
            "latency_avg_ms": avg_ms,
        }

    async def call(self, coro) -> Any:
        """
        Execute une coroutine RPC a travers le circuit breaker.

        - CLOSED: laisse passer, compte les echecs
        - OPEN: refuse immediatement (CircuitOpenError)
        - HALF_OPEN: laisse passer, 1 echec -> OPEN, N succes -> CLOSED
        """
        async with self._lock:
            current_state = self.state

            if current_state == STATE_OPEN:
                raise CircuitOpenError(
                    f"Circuit ouvert pour {self.chain_name} — "
                    f"{self._failures} echecs, dernier: {self._last_failure_error}"
                )

            # Si on passe de OPEN a HALF_OPEN (timeout expire), mettre a jour l'etat
            if current_state == STATE_HALF_OPEN and self._state == STATE_OPEN:
                self._state = STATE_HALF_OPEN
                self._successes_in_half_open = 0
                logger.info(f"[CircuitBreaker] {self.chain_name}: OPEN -> HALF_OPEN (test recuperation)")

        # Executer l'appel RPC (en dehors du lock pour ne pas bloquer)
        start = time.monotonic()
        try:
            result = await coro
            elapsed = time.monotonic() - start

            async with self._lock:
                self._total_calls += 1
                self._record_latency(elapsed)

                if self._state == STATE_HALF_OPEN:
                    self._successes_in_half_open += 1
                    if self._successes_in_half_open >= self.success_to_close:
                        self._state = STATE_CLOSED
                        self._failures = 0
                        self._successes_in_half_open = 0
                        logger.info(
                            f"[CircuitBreaker] {self.chain_name}: HALF_OPEN -> CLOSED "
                            f"({self.success_to_close} succes consecutifs)"
                        )
                elif self._state == STATE_CLOSED:
                    # Reset partiel apres un succes
                    if self._failures > 0:
                        self._failures = max(0, self._failures - 1)

            return result

        except Exception as e:
            elapsed = time.monotonic() - start

            async with self._lock:
                self._total_calls += 1
                self._total_failures += 1
                self._record_latency(elapsed)
                self._failures += 1
                self._last_failure_time = time.monotonic()
                self._last_failure_error = str(e)[:200]  # Tronquer les erreurs longues

                if self._state == STATE_HALF_OPEN:
                    # Un seul echec en half-open -> retour en OPEN
                    self._state = STATE_OPEN
                    logger.warning(
                        f"[CircuitBreaker] {self.chain_name}: HALF_OPEN -> OPEN (echec: {e})"
                    )
                elif self._state == STATE_CLOSED and self._failures >= self.fail_max:
                    self._state = STATE_OPEN
                    logger.error(
                        f"[CircuitBreaker] {self.chain_name}: CLOSED -> OPEN "
                        f"({self._failures}/{self.fail_max} echecs)"
                    )

            raise

    def _record_latency(self, elapsed: float):
        """Enregistre la latence (appeler sous lock)."""
        self._latencies.append(elapsed)
        if len(self._latencies) > self._max_latency_samples:
            self._latencies.pop(0)

    async def reset(self):
        """Reset manuel du circuit breaker (pour admin)."""
        async with self._lock:
            self._state = STATE_CLOSED
            self._failures = 0
            self._successes_in_half_open = 0
            self._last_failure_error = None
            logger.info(f"[CircuitBreaker] {self.chain_name}: reset manuel -> CLOSED")


class CircuitOpenError(Exception):
    """Le circuit est ouvert — la chain est consideree en panne."""
    pass


# ═══════════════════════════════════════════════════════════
#  Instances pre-initialisees pour les 14 chains
# ═══════════════════════════════════════════════════════════

chain_breakers: dict[str, ChainCircuitBreaker] = {
    chain: ChainCircuitBreaker(chain_name=chain)
    for chain in CHAIN_RPC_MAP
}


# ═══════════════════════════════════════════════════════════
#  Appel RPC resilient avec failover + retry exponentiel
# ═══════════════════════════════════════════════════════════

async def resilient_rpc_call(
    chain: str,
    coro,
    fallback_chains: Optional[list[str]] = None,
) -> Any:
    """
    Appel RPC resilient avec circuit breaker + failover.

    1. Tente l'appel sur la chain primaire via son breaker
    2. Si le circuit est OPEN, essaie les fallback chains dans l'ordre
    3. Retry avec backoff exponentiel (1s, 2s, 4s) — max 3 tentatives

    Args:
        chain: nom de la chain primaire (ex: "solana", "base")
        coro: coroutine de l'appel RPC (sera await)
        fallback_chains: liste ordonnee de chains alternatives (None = utilise les defauts)

    Returns:
        Le resultat de l'appel RPC

    Raises:
        Exception: si tous les essais echouent sur toutes les chains
    """
    chain = chain.lower()
    if chain not in chain_breakers:
        raise ValueError(f"Chain inconnue: {chain}. Chains supportees: {list(chain_breakers.keys())}")

    # Construire la liste des chains a essayer
    chains_to_try = [chain]
    if fallback_chains is not None:
        chains_to_try.extend(fallback_chains)
    else:
        chains_to_try.extend(CHAIN_FALLBACKS.get(chain, []))

    last_error: Optional[Exception] = None

    for target_chain in chains_to_try:
        breaker = chain_breakers.get(target_chain)
        if breaker is None:
            continue

        # Verifier si le circuit est ouvert avant de retry inutilement
        if breaker.state == STATE_OPEN:
            logger.debug(f"[Resilient] {target_chain}: circuit ouvert, skip vers fallback")
            continue

        # Retry avec backoff exponentiel sur cette chain
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                result = await breaker.call(coro)
                if target_chain != chain:
                    logger.info(
                        f"[Resilient] Fallback reussi: {chain} -> {target_chain} "
                        f"(tentative {attempt + 1})"
                    )
                return result

            except CircuitOpenError:
                # Circuit vient de s'ouvrir, passer au fallback suivant
                logger.debug(f"[Resilient] {target_chain}: circuit ouvert apres tentative")
                break

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)  # 1s, 2s, 4s
                    logger.warning(
                        f"[Resilient] {target_chain}: echec tentative {attempt + 1}/{MAX_RETRY_ATTEMPTS} "
                        f"({e}), retry dans {delay}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        f"[Resilient] {target_chain}: echec apres {MAX_RETRY_ATTEMPTS} tentatives"
                    )

    # Toutes les chains ont echoue
    error_msg = (
        f"Toutes les chains ont echoue pour l'appel RPC. "
        f"Chains testees: {chains_to_try}. Derniere erreur: {last_error}"
    )
    logger.error(f"[Resilient] {error_msg}")
    raise RuntimeError(error_msg) from last_error


# ═══════════════════════════════════════════════════════════
#  Status global des 14 chains
# ═══════════════════════════════════════════════════════════

def get_all_chain_status() -> dict:
    """
    Retourne le status de toutes les chains.

    Returns:
        dict avec pour chaque chain: {status, failures, latency_avg_ms, last_failure,
        rpc_providers_count, timeout_ms, supports_swap}
    """
    result = {}
    for chain_name, breaker in chain_breakers.items():
        stats = breaker.stats
        current_state = stats["state"]

        # Mapper l'etat du breaker vers un status lisible
        if current_state == STATE_CLOSED:
            status = "ok"
        elif current_state == STATE_HALF_OPEN:
            status = "recovering"
        else:
            status = "down"

        result[chain_name] = {
            "status": status,
            "failures": stats["failures"],
            "total_failures": stats["total_failures"],
            "total_calls": stats["total_calls"],
            "latency_avg_ms": stats["latency_avg_ms"],
            "last_failure": stats["last_failure"],
            "rpc_providers": len(CHAIN_RPC_PROVIDERS.get(chain_name, [])),
            "timeout_ms": CHAIN_TIMEOUT_MS.get(chain_name, 5000),
            "supports_swap": chain_name in SWAP_CHAINS,
        }

    return result


def _compute_overall_status(chain_statuses: dict) -> str:
    """
    Determine le status global:
      - healthy  = toutes les chains ok
      - degraded = 1 a 3 chains down
      - down     = plus de 3 chains down
    """
    down_count = sum(
        1 for info in chain_statuses.values()
        if info["status"] == "down"
    )
    if down_count == 0:
        return "healthy"
    elif down_count <= 3:
        return "degraded"
    else:
        return "down"


# ═══════════════════════════════════════════════════════════
#  FastAPI Router — /status
# ═══════════════════════════════════════════════════════════

router = APIRouter(prefix="/status", tags=["Chain Resilience"])


@router.get("/all")
async def chain_status():
    """Status de toutes les chains (JSON). Pour la page HTML, voir /status."""
    return get_all_chain_status()


@router.get("/chain/{chain_name}")
async def chain_detail(chain_name: str):
    """Detail complet du circuit breaker pour une chain specifique."""
    chain_name = chain_name.lower()
    breaker = chain_breakers.get(chain_name)
    if breaker is None:
        return {
            "error": f"Chain inconnue: {chain_name}",
            "supported_chains": list(chain_breakers.keys()),
        }

    stats = breaker.stats
    rpc_url = CHAIN_RPC_MAP.get(chain_name, "unknown")
    # Masquer l'URL : ne montrer que le hostname (securite: jamais de cles API)
    try:
        from urllib.parse import urlparse
        parsed = urlparse(rpc_url)
        rpc_display = f"{parsed.scheme}://{parsed.hostname}/***"
    except Exception:
        rpc_display = "***"

    # Info providers (masquer les URLs sensibles)
    providers_display = []
    for purl in CHAIN_RPC_PROVIDERS.get(chain_name, []):
        try:
            from urllib.parse import urlparse as _parse
            _p = _parse(purl)
            providers_display.append(f"{_p.scheme}://{_p.hostname}")
        except Exception:
            providers_display.append("***")

    return {
        "chain": chain_name,
        "rpc_endpoint": rpc_display,
        "rpc_providers": providers_display,
        "rpc_providers_count": len(providers_display),
        "timeout_ms": CHAIN_TIMEOUT_MS.get(chain_name, 5000),
        "supports_swap": chain_name in SWAP_CHAINS,
        "circuit_breaker": stats,
        "fallback_chains": CHAIN_FALLBACKS.get(chain_name, []),
        "config": {
            "fail_max": breaker.fail_max,
            "reset_timeout_s": breaker.reset_timeout,
            "success_to_close": breaker.success_to_close,
        },
    }


@router.post("/chain/{chain_name}/reset")
async def chain_reset(chain_name: str, request: Request):
    """Reset manuel du circuit breaker (admin uniquement). Utile apres maintenance."""
    # Auth: admin API key ou appel local
    api_key = request.headers.get("X-API-Key", "")
    ceo_key = os.getenv("CEO_API_KEY", "")
    is_local = request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
    if not is_local and (not api_key or api_key != ceo_key):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin access required")

    chain_name = chain_name.lower()
    breaker = chain_breakers.get(chain_name)
    if breaker is None:
        return {"error": f"Chain inconnue: {chain_name}"}

    await breaker.reset()
    return {
        "message": f"Circuit breaker reset pour {chain_name}",
        "state": breaker.state,
    }


# ═══════════════════════════════════════════════════════════
#  Persistence des stats RPC (snapshot toutes les 5 min)
# ═══════════════════════════════════════════════════════════

_history_snapshots: list[dict] = []  # [{timestamp, chains: {name: {status, latency_avg_ms, failures}}}]
_HISTORY_MAX = 2016  # 7 jours * 24h * 12 snapshots/h (toutes les 5 min)


def snapshot_chain_stats():
    """Prend un snapshot de l'etat de toutes les chains. Appeler toutes les 5 min."""
    snap = {"timestamp": int(time.time()), "chains": {}}
    for chain_name, breaker in chain_breakers.items():
        stats = breaker.stats
        state = stats["state"]
        snap["chains"][chain_name] = {
            "status": "ok" if state == STATE_CLOSED else "recovering" if state == STATE_HALF_OPEN else "down",
            "latency_avg_ms": stats["latency_avg_ms"],
            "failures": stats["total_failures"],
            "calls": stats["total_calls"],
        }
    _history_snapshots.append(snap)
    if len(_history_snapshots) > _HISTORY_MAX:
        _history_snapshots.pop(0)


def get_uptime_stats(hours: int = 24) -> dict:
    """Calcule l'uptime par chain sur les N dernieres heures."""
    cutoff = time.time() - (hours * 3600)
    recent = [s for s in _history_snapshots if s["timestamp"] >= cutoff]
    if not recent:
        return {"period_hours": hours, "snapshots": 0, "chains": {}, "note": "No data yet — stats collected every 5 min"}

    result = {}
    for chain_name in chain_breakers:
        total = 0
        ok_count = 0
        for snap in recent:
            chain_data = snap["chains"].get(chain_name)
            if chain_data:
                total += 1
                if chain_data["status"] == "ok":
                    ok_count += 1
        uptime_pct = round(ok_count / total * 100, 2) if total > 0 else 0
        result[chain_name] = {
            "uptime_pct": uptime_pct,
            "samples": total,
            "down_samples": total - ok_count,
        }
    return {"period_hours": hours, "snapshots": len(recent), "chains": result}


@router.get("/history")
async def chain_history(hours: int = 24):
    """Uptime stats par chain sur les N dernieres heures (defaut 24h)."""
    if hours < 1:
        hours = 1
    if hours > 168:
        hours = 168
    return get_uptime_stats(hours)


@router.get("/history/raw")
async def chain_history_raw(limit: int = 12):
    """Derniers N snapshots bruts (defaut 12 = derniere heure)."""
    if limit < 1:
        limit = 1
    if limit > 288:
        limit = 288
    return {"snapshots": _history_snapshots[-limit:], "total_stored": len(_history_snapshots)}
