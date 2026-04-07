"""MAXIA V12 — Auto-Compound DeFi: automated yield compounding on Solana"""
import logging
import asyncio
import time
import uuid
from fastapi import APIRouter, HTTPException, Header

logger = logging.getLogger("maxia.compound")
router = APIRouter(prefix="/api/compound", tags=["auto-compound"])

# ── Constants ──
PERFORMANCE_FEE_PCT = 5.0   # 5% of yield
COMPOUND_INTERVAL = 3600    # 1 hour
MIN_DEPOSIT, MAX_DEPOSIT = 1.0, 50000.0
APY_CACHE_TTL = 1800        # 30 minutes

# Supported protocols (default_apy = fallback, apy = live from DeFiLlama)
COMPOUND_PROTOCOLS: dict[str, dict] = {
    "marinade":   {"name": "Marinade Finance", "asset": "SOL",  "type": "liquid_staking",
                   "token": "mSOL",    "default_apy": 7.0, "apy": 7.0,
                   "desc": "Liquid staking for SOL — mSOL rewards + MEV.",
                   "url": "https://marinade.finance"},
    "jito":       {"name": "Jito",             "asset": "SOL",  "type": "liquid_staking",
                   "token": "jitoSOL", "default_apy": 8.0, "apy": 8.0,
                   "desc": "MEV-powered liquid staking — highest SOL yields.",
                   "url": "https://www.jito.network"},
    "blazestake": {"name": "BlazeStake",       "asset": "SOL",  "type": "liquid_staking",
                   "token": "bSOL",    "default_apy": 7.0, "apy": 7.0,
                   "desc": "Decentralized liquid staking pool for SOL.",
                   "url": "https://stake.solblaze.org"},
    "kamino":     {"name": "Kamino Finance",   "asset": "USDC", "type": "lending",
                   "token": "kUSDC",   "default_apy": 5.0, "apy": 5.0,
                   "desc": "USDC lending on Kamino — supply interest auto-compounded.",
                   "url": "https://app.kamino.finance"},
}

_apy_cache: dict[str, float] = {}
_apy_cache_ts: float = 0

_LLAMA_KEYS = {
    "marinade": "marinade-liquid-staking_solana_MSOL",
    "jito": "jito-liquid-staking_solana_JITOSOL",
    "blazestake": "blazestake_solana_BSOL",
    "kamino": "kamino-lend_solana_USDC",
}


async def _get_db():
    from core.database import db
    return db


async def _get_agent(api_key: str) -> dict:
    db = await _get_db()
    agent = await db.get_agent(api_key)
    if not agent:
        raise HTTPException(401, "Invalid API key")
    return agent


async def ensure_tables():
    db = await _get_db()
    await db.raw_executescript("""
        CREATE TABLE IF NOT EXISTS compound_vaults (
            vault_id TEXT PRIMARY KEY, api_key TEXT NOT NULL, wallet TEXT NOT NULL,
            protocol TEXT NOT NULL, asset TEXT NOT NULL,
            deposited_usdc NUMERIC(18,6) NOT NULL,
            current_value_usdc NUMERIC(18,6) DEFAULT 0,
            total_yield_usdc NUMERIC(18,6) DEFAULT 0,
            total_compounds INTEGER DEFAULT 0,
            performance_fee_pct NUMERIC(18,6) DEFAULT 5.0,
            status TEXT NOT NULL DEFAULT 'active',
            last_compound INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_vault_api_key ON compound_vaults(api_key);
        CREATE INDEX IF NOT EXISTS idx_vault_status ON compound_vaults(status);
        CREATE INDEX IF NOT EXISTS idx_vault_wallet ON compound_vaults(wallet);
    """)


async def _refresh_apy_rates() -> dict[str, float]:
    """Fetch live APY from DeFiLlama, cache 30 min."""
    global _apy_cache, _apy_cache_ts
    if _apy_cache and time.time() - _apy_cache_ts < APY_CACHE_TTL:
        return _apy_cache
    try:
        from core.http_client import get_http_client
        resp = await get_http_client().get("https://yields.llama.fi/pools", timeout=15)
        resp.raise_for_status()
        idx: dict[str, float] = {}
        for p in resp.json().get("data", []):
            key = f"{p.get('project','').lower()}_{p.get('chain','').lower()}_{(p.get('symbol') or '').upper()}"
            apy = p.get("apy", 0) or 0
            if key not in idx or apy > idx[key]:
                idx[key] = apy
        rates: dict[str, float] = {}
        for pid, llama_key in _LLAMA_KEYS.items():
            live = idx.get(llama_key, 0)
            if live > 0:
                rates[pid] = round(live, 2)
                COMPOUND_PROTOCOLS[pid]["apy"] = rates[pid]
            else:
                rates[pid] = COMPOUND_PROTOCOLS[pid]["default_apy"]
        _apy_cache, _apy_cache_ts = rates, time.time()
        logger.info("[Compound] APY refreshed: %s", rates)
        return rates
    except Exception as e:
        logger.warning("[Compound] APY refresh failed: %s", e)
        return _apy_cache or {pid: p["default_apy"] for pid, p in COMPOUND_PROTOCOLS.items()}


def _net_apy(gross: float) -> float:
    return round(gross * (1 - PERFORMANCE_FEE_PCT / 100), 2)


# ── API Endpoints ──

@router.post("/deposit")
async def compound_deposit(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Create an auto-compound vault position."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)
    protocol = (req.get("protocol") or "").lower()
    amount_usdc = float(req.get("amount_usdc", 0))
    wallet = req.get("wallet", "")
    if protocol not in COMPOUND_PROTOCOLS:
        raise HTTPException(400, f"Invalid protocol. Supported: {sorted(COMPOUND_PROTOCOLS.keys())}")
    proto = COMPOUND_PROTOCOLS[protocol]
    asset_in = (req.get("asset") or "").upper()
    if asset_in and asset_in != proto["asset"]:
        raise HTTPException(400, f"{proto['name']} only supports {proto['asset']}")
    if amount_usdc < MIN_DEPOSIT:
        raise HTTPException(400, f"Minimum deposit: {MIN_DEPOSIT} USDC")
    if amount_usdc > MAX_DEPOSIT:
        raise HTTPException(400, f"Maximum deposit: {MAX_DEPOSIT} USDC")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Valid wallet address required (min 20 chars)")

    vault_id, now = str(uuid.uuid4()), int(time.time())
    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO compound_vaults(vault_id,api_key,wallet,protocol,asset,"
        "deposited_usdc,current_value_usdc,performance_fee_pct,last_compound) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (vault_id, x_api_key, wallet, protocol, proto["asset"],
         amount_usdc, amount_usdc, PERFORMANCE_FEE_PCT, now))
    rates = await _refresh_apy_rates()
    apy = rates.get(protocol, proto["default_apy"])
    logger.info("[Compound] Vault %s: %.2f USDC in %s for %s", vault_id[:8], amount_usdc, proto["name"], wallet[:8])
    return {"success": True, "vault_id": vault_id, "protocol": proto["name"],
            "asset": proto["asset"], "deposited_usdc": amount_usdc, "current_value_usdc": amount_usdc,
            "current_apy_percent": apy, "performance_fee_percent": PERFORMANCE_FEE_PCT,
            "net_apy_percent": _net_apy(apy), "compound_interval": "hourly", "status": "active", "wallet": wallet}


@router.get("/my")
async def compound_my_vaults(x_api_key: str = Header(None, alias="X-API-Key")):
    """List my auto-compound vaults."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT vault_id,api_key,wallet,protocol,asset,deposited_usdc,current_value_usdc,"
        "total_yield_usdc,total_compounds,performance_fee_pct,status,last_compound,created_at "
        "FROM compound_vaults WHERE api_key=? ORDER BY created_at DESC", (x_api_key,))
    vaults, rates = [], await _refresh_apy_rates()
    for r in rows:
        row = dict(r)
        pid = row.get("protocol", "")
        apy = rates.get(pid, COMPOUND_PROTOCOLS.get(pid, {}).get("default_apy", 0))
        dep = float(row.get("deposited_usdc", 0) or 0)
        val = float(row.get("current_value_usdc", 0) or 0)
        row.update({"current_apy_percent": apy, "net_apy_percent": _net_apy(apy),
                     "gain_usdc": round(val - dep, 6) if val > dep else 0.0,
                     "protocol_name": COMPOUND_PROTOCOLS.get(pid, {}).get("name", pid)})
        vaults.append(row)
    return {"vaults": vaults, "total": len(vaults)}


@router.get("/protocols")
async def compound_protocols():
    """Public: list available auto-compound protocols with live APY."""
    rates = await _refresh_apy_rates()
    protocols = []
    for pid, p in COMPOUND_PROTOCOLS.items():
        apy = rates.get(pid, p["default_apy"])
        protocols.append({"protocol_id": pid, "name": p["name"], "asset": p["asset"],
                          "type": p["type"], "token": p["token"], "gross_apy_percent": apy,
                          "performance_fee_percent": PERFORMANCE_FEE_PCT, "net_apy_percent": _net_apy(apy),
                          "compound_interval": "hourly", "description": p["desc"], "url": p["url"]})
    protocols.sort(key=lambda x: x["net_apy_percent"], reverse=True)
    return {"protocols": protocols, "total": len(protocols)}


@router.delete("/{vault_id}")
async def compound_withdraw(vault_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Withdraw and close an auto-compound vault."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT vault_id,status,current_value_usdc,deposited_usdc,total_yield_usdc "
        "FROM compound_vaults WHERE vault_id=? AND api_key=?", (vault_id, x_api_key))
    if not rows:
        raise HTTPException(404, "Vault not found or not owned by you")
    vault = dict(rows[0])
    if vault.get("status") == "closed":
        raise HTTPException(400, "Vault already closed")
    await db.raw_execute("UPDATE compound_vaults SET status='closed' WHERE vault_id=? AND api_key=?",
                         (vault_id, x_api_key))
    final = float(vault.get("current_value_usdc", 0) or 0)
    dep = float(vault.get("deposited_usdc", 0) or 0)
    yld = float(vault.get("total_yield_usdc", 0) or 0)
    logger.info("[Compound] Closed %s — dep %.2f, final %.2f, yield %.2f", vault_id[:8], dep, final, yld)
    return {"success": True, "vault_id": vault_id, "status": "closed",
            "deposited_usdc": dep, "final_value_usdc": final,
            "total_yield_usdc": yld, "net_profit_usdc": round(final - dep, 6)}


@router.get("/stats")
async def compound_stats():
    """Public auto-compound stats — no auth required."""
    try:
        db = await _get_db()
        active = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM compound_vaults WHERE status='active'")
        totals = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(current_value_usdc),0) as tvl,"
            "COALESCE(SUM(total_yield_usdc),0) as yield_total,"
            "COALESCE(SUM(total_compounds),0) as compounds "
            "FROM compound_vaults WHERE status IN ('active','closed')")
        cnt = dict(active[0]).get("cnt", 0) if active else 0
        row = dict(totals[0]) if totals else {}
        rates = await _refresh_apy_rates()
        return {"active_vaults": cnt,
                "total_value_locked_usdc": round(float(row.get("tvl", 0)), 2),
                "total_yield_generated_usdc": round(float(row.get("yield_total", 0)), 2),
                "total_compounds": int(row.get("compounds", 0)),
                "performance_fee_percent": PERFORMANCE_FEE_PCT,
                "supported_protocols": sorted(COMPOUND_PROTOCOLS.keys()),
                "compound_interval": "hourly",
                "current_apys": {pid: rates.get(pid, p["default_apy"]) for pid, p in COMPOUND_PROTOCOLS.items()}}
    except Exception as e:
        from core.error_utils import safe_error
        logger.error("[Compound] Stats error: %s", e)
        return {"active_vaults": 0, "total_value_locked_usdc": 0,
                "supported_protocols": sorted(COMPOUND_PROTOCOLS.keys()), "error": safe_error(e)}


# ── Background Worker ──

async def compound_worker():
    """Background: auto-compound yields every hour for active vaults."""
    logger.info("[Compound] Worker started — interval %ds", COMPOUND_INTERVAL)
    while True:
        try:
            await asyncio.sleep(COMPOUND_INTERVAL)
            db, now = await _get_db(), int(time.time())
            active = await db.raw_execute_fetchall(
                "SELECT vault_id,protocol,current_value_usdc,total_yield_usdc,"
                "total_compounds,performance_fee_pct,last_compound "
                "FROM compound_vaults WHERE status='active'")
            if not active:
                continue
            rates = await _refresh_apy_rates()
            for row in active:
                v = dict(row)
                vid, proto = v["vault_id"], v["protocol"]
                cur_val = float(v.get("current_value_usdc", 0) or 0)
                last = int(v.get("last_compound", 0) or 0)
                fee_pct = float(v.get("performance_fee_pct", PERFORMANCE_FEE_PCT) or PERFORMANCE_FEE_PCT)
                try:
                    apy = rates.get(proto, COMPOUND_PROTOCOLS.get(proto, {}).get("default_apy", 0))
                    if apy <= 0:
                        continue
                    elapsed = min(now - last if last > 0 else COMPOUND_INTERVAL, COMPOUND_INTERVAL * 2)
                    gross = cur_val * (apy / 100 / 8760) * (elapsed / 3600)
                    if gross < 0.000001:
                        continue
                    fee = gross * fee_pct / 100
                    net = gross - fee
                    new_val = round(cur_val + net, 6)
                    new_yield = round(float(v.get("total_yield_usdc", 0) or 0) + net, 6)
                    new_cnt = int(v.get("total_compounds", 0) or 0) + 1
                    await db.raw_execute(
                        "UPDATE compound_vaults SET current_value_usdc=?,"
                        "total_yield_usdc=?,total_compounds=?,last_compound=? WHERE vault_id=?",
                        (new_val, new_yield, new_cnt, now, vid))
                    logger.debug("[Compound] %s: +$%.6f (fee $%.6f) val=$%.2f apy=%.2f%% #%d",
                                 vid[:8], net, fee, new_val, apy, new_cnt)
                except Exception as e:
                    logger.error("[Compound] Vault %s error: %s", vid[:8], e)
                await asyncio.sleep(0.1)
            logger.info("[Compound] Cycle done — %d vaults", len(active))
        except Exception as e:
            logger.error("[Compound] Worker error: %s", e)


# ── Wallet-based Endpoints (frontend dashboard) ──

def _validate_wallet(wallet: str) -> str:
    """Valide et retourne l'adresse wallet Solana."""
    if not wallet or len(wallet) < 32 or len(wallet) > 44:
        raise HTTPException(400, "Valid Solana wallet address required (32-44 chars)")
    return wallet


@router.post("/w/deposit")
async def compound_wallet_deposit(req: dict, x_wallet: str = Header(None, alias="X-Wallet")):
    """Creer un vault auto-compound via wallet (dashboard)."""
    wallet = _validate_wallet(x_wallet or req.get("wallet", ""))
    protocol = (req.get("protocol") or "").lower()
    amount_usdc = float(req.get("amount_usdc", 0))
    if protocol not in COMPOUND_PROTOCOLS:
        raise HTTPException(400, f"Invalid protocol. Supported: {sorted(COMPOUND_PROTOCOLS.keys())}")
    proto = COMPOUND_PROTOCOLS[protocol]
    if amount_usdc < MIN_DEPOSIT:
        raise HTTPException(400, f"Minimum deposit: {MIN_DEPOSIT} USDC")
    if amount_usdc > MAX_DEPOSIT:
        raise HTTPException(400, f"Maximum deposit: {MAX_DEPOSIT} USDC")

    vault_id, now = str(uuid.uuid4()), int(time.time())
    synthetic_key = f"wallet:{wallet[:16]}"
    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO compound_vaults(vault_id,api_key,wallet,protocol,asset,"
        "deposited_usdc,current_value_usdc,performance_fee_pct,last_compound) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (vault_id, synthetic_key, wallet, protocol, proto["asset"],
         amount_usdc, amount_usdc, PERFORMANCE_FEE_PCT, now))
    rates = await _refresh_apy_rates()
    apy = rates.get(protocol, proto["default_apy"])
    logger.info("[Compound] Wallet vault %s: %.2f USDC in %s for %s", vault_id[:8], amount_usdc, proto["name"], wallet[:8])
    return {"success": True, "vault_id": vault_id, "protocol": proto["name"],
            "asset": proto["asset"], "deposited_usdc": amount_usdc, "current_value_usdc": amount_usdc,
            "current_apy_percent": apy, "performance_fee_percent": PERFORMANCE_FEE_PCT,
            "net_apy_percent": _net_apy(apy), "compound_interval": "hourly", "status": "active", "wallet": wallet}


@router.get("/w/my")
async def compound_wallet_my_vaults(x_wallet: str = Header(None, alias="X-Wallet")):
    """Lister mes vaults auto-compound (auth wallet)."""
    wallet = _validate_wallet(x_wallet or "")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT vault_id,api_key,wallet,protocol,asset,deposited_usdc,current_value_usdc,"
        "total_yield_usdc,total_compounds,performance_fee_pct,status,last_compound,created_at "
        "FROM compound_vaults WHERE wallet=? ORDER BY created_at DESC", (wallet,))
    vaults, rates = [], await _refresh_apy_rates()
    for r in rows:
        row = dict(r)
        pid = row.get("protocol", "")
        apy = rates.get(pid, COMPOUND_PROTOCOLS.get(pid, {}).get("default_apy", 0))
        dep = float(row.get("deposited_usdc", 0) or 0)
        val = float(row.get("current_value_usdc", 0) or 0)
        row.update({"current_apy_percent": apy, "net_apy_percent": _net_apy(apy),
                     "gain_usdc": round(val - dep, 6) if val > dep else 0.0,
                     "protocol_name": COMPOUND_PROTOCOLS.get(pid, {}).get("name", pid)})
        vaults.append(row)
    return {"vaults": vaults, "total": len(vaults)}


@router.delete("/w/{vault_id}")
async def compound_wallet_withdraw(vault_id: str, x_wallet: str = Header(None, alias="X-Wallet")):
    """Fermer un vault auto-compound (auth wallet)."""
    wallet = _validate_wallet(x_wallet or "")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT vault_id,wallet,status,current_value_usdc,deposited_usdc,total_yield_usdc "
        "FROM compound_vaults WHERE vault_id=? AND wallet=?", (vault_id, wallet))
    if not rows:
        raise HTTPException(404, "Vault not found or not owned by this wallet")
    vault = dict(rows[0])
    if vault.get("status") == "closed":
        raise HTTPException(400, "Vault already closed")
    await db.raw_execute("UPDATE compound_vaults SET status='closed' WHERE vault_id=? AND wallet=?",
                         (vault_id, wallet))
    final = float(vault.get("current_value_usdc", 0) or 0)
    dep = float(vault.get("deposited_usdc", 0) or 0)
    yld = float(vault.get("total_yield_usdc", 0) or 0)
    logger.info("[Compound] Wallet closed %s — dep %.2f, final %.2f", vault_id[:8], dep, final)
    return {"success": True, "vault_id": vault_id, "status": "closed",
            "deposited_usdc": dep, "final_value_usdc": final,
            "total_yield_usdc": yld, "net_profit_usdc": round(final - dep, 6)}


@router.get("/w/tx/{vault_id}")
async def compound_wallet_build_tx(vault_id: str, x_wallet: str = Header(None, alias="X-Wallet")):
    """Construire une transaction Solana non-signee pour le depot initial du vault."""
    wallet = _validate_wallet(x_wallet or "")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT vault_id,wallet,protocol,asset,deposited_usdc,status "
        "FROM compound_vaults WHERE vault_id=? AND wallet=?", (vault_id, wallet))
    if not rows:
        raise HTTPException(404, "Vault not found or not owned by this wallet")
    vault = dict(rows[0])
    if vault.get("status") == "closed":
        raise HTTPException(400, "Vault is closed")
    protocol = vault["protocol"]
    amount = float(vault.get("deposited_usdc", 0))

    try:
        from trading.solana_defi import (
            _marinade_build_stake_tx, _build_staking_via_jupiter,
            _build_kamino_lend_tx, JITOSOL_MINT, BSOL_MINT,
        )
        if protocol == "marinade":
            result = await _marinade_build_stake_tx(amount, wallet)
        elif protocol == "jito":
            result = await _build_staking_via_jupiter(amount, wallet, JITOSOL_MINT, "Jito")
        elif protocol == "blazestake":
            result = await _build_staking_via_jupiter(amount, wallet, BSOL_MINT, "BlazeStake")
        elif protocol == "kamino":
            result = await _build_kamino_lend_tx("USDC", amount, wallet, "lend")
        else:
            raise HTTPException(400, f"No tx builder for protocol: {protocol}")
        return {"success": True, "vault_id": vault_id, "protocol": protocol, **result}
    except HTTPException:
        raise
    except Exception as e:
        from core.error_utils import safe_error
        logger.error("[Compound] TX build error for %s: %s", vault_id[:8], e)
        # Fallback : instructions manuelles
        proto = COMPOUND_PROTOCOLS[protocol]
        return {"success": True, "vault_id": vault_id, "protocol": protocol,
                "method": "manual", "transaction_b64": None,
                "steps": [f"1. Go to {proto['url']}", f"2. Connect wallet {wallet[:8]}...",
                          f"3. Deposit {amount} {proto['asset']}", "4. Confirm in your wallet"]}


def get_router():
    return router
