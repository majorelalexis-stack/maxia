"""MAXIA Hub R1 — Boost réputation on-chain externe.

Interroge Solana (getSignaturesForAddress) et Base (eth_getTransactionCount)
pour évaluer l'historique wallet hors MAXIA.
Boost additif plafonné à +15 points stocké dans hub_agents.score_r1_boost.
"""
import time
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException

from core.config import BASE_RPC, SOLANA_RPC_URLS, SOLANA_RPC
from core.database import db

_SOLANA_RPC = SOLANA_RPC_URLS[0] if SOLANA_RPC_URLS else SOLANA_RPC
_BASE_RPC = BASE_RPC
_HTTP_TIMEOUT = 12.0

_TX_SATURATION = 200       # nb de txs pour atteindre le max de contribution tx
_AGE_SATURATION_DAYS = 365  # ancienneté pour atteindre le max de contribution age
_BOOST_MAX = 15.0
_WEIGHT_TX = 0.6
_WEIGHT_AGE = 0.4

_SOLANA_CHAINS = {"solana", "sol"}
_BASE_CHAINS = {"base", "eth", "ethereum", "polygon", "arbitrum", "avalanche", "bnb"}


# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class WalletActivity:
    chain: str
    wallet: str
    tx_count: int
    wallet_age_days: int
    error: str | None = field(default=None)


# ─── Fetcher ─────────────────────────────────────────────────────────────────

class WalletHistoryFetcher:
    async def fetch_solana(self, wallet: str, http_client: httpx.AsyncClient) -> WalletActivity:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": 1000}],
        }
        try:
            resp = await http_client.post(_SOLANA_RPC, json=payload, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return WalletActivity(chain="solana", wallet=wallet, tx_count=0,
                                      wallet_age_days=0, error=f"http {resp.status_code}")
            sigs = resp.json().get("result", [])
            tx_count = len(sigs)
            if sigs:
                oldest_ts = min(s["blockTime"] for s in sigs if s.get("blockTime"))
                age_days = max(0, int((time.time() - oldest_ts) / 86400))
            else:
                age_days = 0
            return WalletActivity(chain="solana", wallet=wallet,
                                  tx_count=tx_count, wallet_age_days=age_days)
        except Exception as exc:
            return WalletActivity(chain="solana", wallet=wallet,
                                  tx_count=0, wallet_age_days=0, error=str(exc))

    async def fetch_base(self, wallet: str, http_client: httpx.AsyncClient) -> WalletActivity:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionCount",
            "params": [wallet, "latest"],
        }
        try:
            resp = await http_client.post(_BASE_RPC, json=payload, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return WalletActivity(chain="base", wallet=wallet, tx_count=0,
                                      wallet_age_days=0, error=f"http {resp.status_code}")
            hex_count = resp.json().get("result", "0x0")
            tx_count = int(hex_count, 16)
            return WalletActivity(chain="base", wallet=wallet,
                                  tx_count=tx_count, wallet_age_days=0)
        except Exception as exc:
            return WalletActivity(chain="base", wallet=wallet,
                                  tx_count=0, wallet_age_days=0, error=str(exc))


# ─── Boost computation ───────────────────────────────────────────────────────

def compute_r1_boost(activity: WalletActivity) -> float:
    if activity.error or (activity.tx_count == 0 and activity.wallet_age_days == 0):
        return 0.0
    tx_score = min(1.0, activity.tx_count / _TX_SATURATION)
    age_score = min(1.0, activity.wallet_age_days / _AGE_SATURATION_DAYS)
    raw = (_WEIGHT_TX * tx_score + _WEIGHT_AGE * age_score) * _BOOST_MAX
    return min(_BOOST_MAX, round(raw, 4))


# ─── Apply boost ─────────────────────────────────────────────────────────────

async def apply_r1_boost(db, hub_id: str, http_client: httpx.AsyncClient) -> dict:
    row = await db._fetchone(
        "SELECT hub_id, wallet, chain FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")

    row = dict(row)
    chain = row["chain"].lower()
    wallet = row["wallet"]

    fetcher = WalletHistoryFetcher()
    if chain in _SOLANA_CHAINS:
        activity = await fetcher.fetch_solana(wallet, http_client)
    elif chain in _BASE_CHAINS:
        activity = await fetcher.fetch_base(wallet, http_client)
    else:
        activity = WalletActivity(chain=chain, wallet=wallet, tx_count=0,
                                  wallet_age_days=0, error=f"chain {chain!r} not supported")

    boost = compute_r1_boost(activity)

    await db.raw_execute(
        "UPDATE hub_agents SET score_r1_boost=? WHERE hub_id=?",
        (boost, hub_id),
    )

    return {
        "hub_id": hub_id,
        "chain": chain,
        "wallet": wallet,
        "tx_count": activity.tx_count,
        "wallet_age_days": activity.wallet_age_days,
        "boost": boost,
        "error": activity.error,
    }


# ─── Router ──────────────────────────────────────────────────────────────────

r1_router = APIRouter(prefix="/api/hub/r1", tags=["hub-r1"])


@r1_router.post("/refresh/{hub_id}", status_code=202)
async def refresh_r1(hub_id: str, background_tasks: BackgroundTasks):
    """Déclenche le calcul du boost R1 en arrière-plan."""
    async def _task():
        async with httpx.AsyncClient() as client:
            await apply_r1_boost(db, hub_id, client)

    background_tasks.add_task(_task)
    return {"status": "running", "hub_id": hub_id}


@r1_router.get("/{hub_id}")
async def get_r1_detail(hub_id: str):
    """Retourne le boost R1 stocké pour un agent."""
    row = await db._fetchone(
        "SELECT hub_id, wallet, chain, score_r1_boost FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")
    r = dict(row)
    return {
        "hub_id": r["hub_id"],
        "chain": r["chain"],
        "wallet": r["wallet"],
        "score_r1_boost": r.get("score_r1_boost", 0.0),
    }
