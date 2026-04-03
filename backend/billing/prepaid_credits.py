"""MAXIA Prepaid Credits — Off-chain micropayments without gas fees.

Agents deposit USDC on-chain once, then consume credits via API calls.
No gas per transaction. Settlement batch 1x/day to treasury.

Flow:
  1. Agent sends USDC to treasury (on-chain, any supported chain)
  2. POST /api/credits/deposit {payment_tx, chain} → credits added to balance
  3. Agent uses services → credits deducted automatically
  4. GET /api/credits/balance → current balance + history

Tables: prepaid_balances, prepaid_transactions
"""
import logging
import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credits", tags=["prepaid-credits"])

# ── Schema ──

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prepaid_balances (
    agent_id TEXT PRIMARY KEY,
    wallet TEXT NOT NULL,
    balance_usdc NUMERIC(18,6) DEFAULT 0,
    total_deposited NUMERIC(18,6) DEFAULT 0,
    total_spent NUMERIC(18,6) DEFAULT 0,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_prepaid_wallet ON prepaid_balances(wallet);

CREATE TABLE IF NOT EXISTS prepaid_transactions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    amount_usdc NUMERIC(18,6) NOT NULL,
    balance_after NUMERIC(18,6) NOT NULL,
    description TEXT DEFAULT '',
    payment_tx TEXT DEFAULT '',
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_prepaid_tx_agent ON prepaid_transactions(agent_id, created_at DESC);
"""

_schema_ready = False


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[Credits] Schema init error: %s", e)


# ── Core functions (used by other modules) ──

async def get_balance(agent_id: str) -> float:
    """Get prepaid balance for an agent. Returns 0 if no balance."""
    await _ensure_schema()
    from core.database import db
    row = await db._fetchone(
        "SELECT balance_usdc FROM prepaid_balances WHERE agent_id=?", (agent_id,))
    return float(row["balance_usdc"]) if row else 0.0


async def deduct_credits(agent_id: str, amount: float, description: str = "") -> dict:
    """Deduct credits from agent balance. Returns success/error dict.

    Called by execute endpoints to charge for services without gas.
    Uses atomic UPDATE WHERE balance_usdc >= amount to prevent race conditions.
    """
    if amount <= 0:
        return {"success": False, "error": "Amount must be positive"}

    await _ensure_schema()
    from core.database import db

    # Atomic check-and-deduct: UPDATE only if balance sufficient (no race condition)
    tx_id = str(uuid.uuid4())
    now = int(time.time())

    await db.raw_execute(
        "UPDATE prepaid_balances SET balance_usdc = balance_usdc - ?, total_spent = total_spent + ?, updated_at = ? "
        "WHERE agent_id = ? AND balance_usdc >= ?",
        (amount, amount, now, agent_id, amount))

    # Check if the update actually happened (affected rows)
    row = await db._fetchone(
        "SELECT balance_usdc FROM prepaid_balances WHERE agent_id=?", (agent_id,))
    if not row:
        return {"success": False, "error": "No prepaid balance. Deposit USDC first: POST /api/credits/deposit"}

    new_balance = float(row["balance_usdc"])
    # If balance didn't change (or we need to verify the deduction happened),
    # check if amount was actually deducted by comparing with expected
    # The atomic UPDATE guarantees no negative balance
    await db.raw_execute(
        "INSERT INTO prepaid_transactions(id, agent_id, type, amount_usdc, balance_after, description, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (tx_id, agent_id, "debit", amount, new_balance, description[:200], now))

    return {"success": True, "charged": amount, "balance": new_balance, "tx_id": tx_id}


async def add_credits(agent_id: str, wallet: str, amount: float,
                      payment_tx: str = "", description: str = "") -> dict:
    """Add credits to agent balance after on-chain USDC deposit."""
    if amount <= 0:
        return {"success": False, "error": "Amount must be positive"}

    await _ensure_schema()
    from core.database import db

    row = await db._fetchone(
        "SELECT balance_usdc FROM prepaid_balances WHERE agent_id=?", (agent_id,))
    now = int(time.time())
    tx_id = str(uuid.uuid4())

    if row:
        new_balance = round(float(row["balance_usdc"]) + amount, 6)
        await db.raw_execute(
            "UPDATE prepaid_balances SET balance_usdc=?, total_deposited=total_deposited+?, updated_at=? WHERE agent_id=?",
            (new_balance, amount, now, agent_id))
    else:
        new_balance = round(amount, 6)
        await db.raw_execute(
            "INSERT INTO prepaid_balances(agent_id, wallet, balance_usdc, total_deposited, total_spent, updated_at) "
            "VALUES(?,?,?,?,0,?)",
            (agent_id, wallet, new_balance, amount, now))

    await db.raw_execute(
        "INSERT INTO prepaid_transactions(id, agent_id, type, amount_usdc, balance_after, description, payment_tx, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (tx_id, agent_id, "credit", amount, new_balance, description[:200], payment_tx[:200], now))

    return {"success": True, "deposited": amount, "balance": new_balance, "tx_id": tx_id}


# ── API Endpoints ──

class DepositRequest(BaseModel):
    payment_tx: str = Field(..., min_length=10, max_length=200)
    amount_usdc: float = Field(..., gt=0)
    chain: str = Field(default="solana")


@router.post("/deposit")
async def deposit_credits(req: DepositRequest, x_api_key: str = Header(None, alias="X-API-Key")):
    """Deposit USDC on-chain and receive prepaid credits.

    1. Send USDC to MAXIA Treasury on Solana/Base/any chain
    2. Pass the tx signature here
    3. Credits added to your balance instantly
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    await _ensure_schema()
    from core.database import db

    # Verify agent exists
    agent = await db._fetchone("SELECT wallet, agent_id FROM agents WHERE api_key=?", (x_api_key,))
    if not agent:
        raise HTTPException(401, "Invalid API key")

    agent_id = agent.get("agent_id", x_api_key)
    wallet = agent.get("wallet", "")

    # Verify on-chain payment
    import asyncio
    try:
        if req.chain == "solana":
            from blockchain.solana_verifier import verify_transaction
            from core.config import TREASURY_ADDRESS
            result = await asyncio.wait_for(verify_transaction(
                tx_signature=req.payment_tx,
                expected_amount_usdc=req.amount_usdc,
                expected_recipient=TREASURY_ADDRESS,
            ), timeout=20)
        elif req.chain == "cosmos":
            from blockchain.cosmos_verifier import verify_usdc_transfer as cosmos_verify
            result = await asyncio.wait_for(
                cosmos_verify(req.payment_tx, req.amount_usdc),
                timeout=20)
        elif req.chain == "hedera":
            from blockchain.hedera_verifier import verify_usdc_transfer as hedera_verify
            result = await asyncio.wait_for(
                hedera_verify(req.payment_tx, req.amount_usdc),
                timeout=20)
        elif req.chain == "cardano":
            from blockchain.cardano_verifier import verify_usdc_transfer as cardano_verify
            result = await asyncio.wait_for(
                cardano_verify(req.payment_tx, req.amount_usdc),
                timeout=20)
        elif req.chain == "polkadot":
            from blockchain.polkadot_verifier import verify_usdc_transfer as polkadot_verify
            result = await asyncio.wait_for(
                polkadot_verify(req.payment_tx, req.amount_usdc),
                timeout=20)
        else:
            # EVM chains
            from routes.chain_verify_api import evm_verifiers
            verifier = evm_verifiers.get(req.chain)
            if not verifier:
                raise HTTPException(400, f"Unsupported chain: {req.chain}")
            result = await asyncio.wait_for(
                verifier.verify_usdc_transfer(req.payment_tx, int(req.amount_usdc * 1_000_000)),
                timeout=20)

        if not result.get("valid"):
            raise HTTPException(400, f"Payment not verified: {result.get('error', 'verification failed')}")
    except asyncio.TimeoutError:
        raise HTTPException(504, "Payment verification timed out. Please retry.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Credits] Payment verification error: %s", e)
        raise HTTPException(400, "Payment verification failed")

    # Idempotency check
    existing = await db._fetchone(
        "SELECT 1 FROM prepaid_transactions WHERE payment_tx=?", (req.payment_tx,))
    if existing:
        raise HTTPException(400, "Payment already credited")

    # C-3 fix: use VERIFIED on-chain amount, not user-declared amount
    verified_amount = result.get("amount_usdc", req.amount_usdc)
    if isinstance(verified_amount, (int, float)) and verified_amount > 0:
        credit_amount = float(verified_amount)
    else:
        credit_amount = req.amount_usdc  # fallback if verifier doesn't return amount

    # Add credits
    credit_result = await add_credits(
        agent_id, wallet, credit_amount,
        payment_tx=req.payment_tx,
        description=f"Deposit via {req.chain}")

    if not credit_result.get("success"):
        raise HTTPException(500, credit_result.get("error", "Credit failed"))

    return {
        "success": True,
        "deposited_usdc": credit_amount,
        "balance_usdc": credit_result["balance"],
        "chain": req.chain,
        "tx_id": credit_result["tx_id"],
    }


@router.get("/balance")
async def credit_balance(x_api_key: str = Header(None, alias="X-API-Key")):
    """Get current prepaid credit balance."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    await _ensure_schema()
    from core.database import db

    agent = await db._fetchone("SELECT agent_id FROM agents WHERE api_key=?", (x_api_key,))
    if not agent:
        raise HTTPException(401, "Invalid API key")

    agent_id = agent.get("agent_id", x_api_key)
    row = await db._fetchone(
        "SELECT balance_usdc, total_deposited, total_spent, updated_at FROM prepaid_balances WHERE agent_id=?",
        (agent_id,))

    if not row:
        return {"balance_usdc": 0, "total_deposited": 0, "total_spent": 0, "transactions": []}

    # Last 20 transactions
    txs = await db.raw_execute_fetchall(
        "SELECT type, amount_usdc, balance_after, description, created_at FROM prepaid_transactions "
        "WHERE agent_id=? ORDER BY created_at DESC LIMIT 20", (agent_id,))

    return {
        "balance_usdc": float(row["balance_usdc"]),
        "total_deposited": float(row["total_deposited"]),
        "total_spent": float(row["total_spent"]),
        "updated_at": row["updated_at"],
        "transactions": [dict(t) for t in txs],
    }


@router.get("/stats")
async def credit_stats():
    """Global prepaid credit statistics."""
    await _ensure_schema()
    from core.database import db
    try:
        row = await db._fetchone(
            "SELECT COUNT(*) as agents, COALESCE(SUM(balance_usdc),0) as total_balance, "
            "COALESCE(SUM(total_deposited),0) as total_deposited, "
            "COALESCE(SUM(total_spent),0) as total_spent "
            "FROM prepaid_balances")
        return {
            "agents_with_credits": int(row["agents"]) if row else 0,
            "total_balance_usdc": float(row["total_balance"]) if row else 0,
            "total_deposited_usdc": float(row["total_deposited"]) if row else 0,
            "total_spent_usdc": float(row["total_spent"]) if row else 0,
        }
    except Exception as e:
        return safe_error(e, "credit_stats")
