"""
chain_api.py — Non-EVM chain verification routes.

Extracted from main.py: XRPL, TON, SUI, TRON, NEAR, Aptos, SEI.
"""

from fastapi import APIRouter, HTTPException, Request
from error_utils import safe_error
from security import check_rate_limit

router = APIRouter(tags=["chains"])


# ══════════════════════════════════════════════════════════
#  V12: XRP LEDGER (4eme reseau)
# ══════════════════════════════════════════════════════════

@router.post("/api/xrpl/verify")
async def xrpl_verify(request: Request):
    """Verifie une transaction sur XRP Ledger."""
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from xrpl_verifier import verify_xrpl_transaction
        return await verify_xrpl_transaction(
            tx_hash,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}


@router.get("/api/xrpl/balance/{address}")
async def xrpl_balance(address: str):
    """Solde XRP + USDC d'un wallet XRPL."""
    try:
        from xrpl_verifier import get_xrpl_balance
        return await get_xrpl_balance(address)
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/api/xrpl/info")
async def xrpl_info():
    """Infos XRP Ledger."""
    from config import XRPL_RPC, XRPL_USDC_ISSUER, TREASURY_ADDRESS_XRPL
    return {
        "network": "xrpl-mainnet",
        "rpc": XRPL_RPC,
        "usdc_issuer": XRPL_USDC_ISSUER,
        "treasury": TREASURY_ADDRESS_XRPL or "not configured",
        "supported_currencies": ["XRP", "USDC"],
        "settlement_time": "3-5 seconds",
        "fees": "< $0.01",
    }


@router.post("/api/xrpl/verify-usdc")
async def xrpl_verify_usdc(request: Request):
    """Verifie un transfert USDC sur XRPL."""
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from xrpl_verifier import verify_usdc_transfer_xrpl
        return await verify_usdc_transfer_xrpl(
            tx_hash,
            expected_dest=body.get("expected_dest", ""),
            min_amount=float(body.get("min_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}


# ══════════════════════════════════════════════════════════
#  V12: TON — The Open Network (5eme reseau, non-EVM)
# ══════════════════════════════════════════════════════════

@router.get("/api/ton/info")
async def ton_info():
    """Infos reseau TON."""
    from config import TON_API_URL, TREASURY_ADDRESS_TON, TON_USDT_JETTON
    return {
        "network": "ton-mainnet",
        "api": TON_API_URL,
        "usdt_jetton": TON_USDT_JETTON,
        "treasury": TREASURY_ADDRESS_TON or "not configured",
        "status": "active" if TREASURY_ADDRESS_TON else "not_configured",
        "supported_currencies": ["TON", "USDT"],
        "settlement_time": "5-10 seconds",
        "fees": "< $0.01",
        "note": "TON uses USDT (Tether) — no native USDC on TON yet",
    }


@router.post("/api/ton/verify")
async def ton_verify(request: Request):
    """Verifie une transaction sur TON."""
    await check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from ton_verifier import verify_ton_transaction
        return await verify_ton_transaction(
            tx_hash,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}


@router.get("/api/ton/balance/{address}")
async def ton_balance(address: str):
    """Solde TON d'un wallet."""
    try:
        from ton_verifier import get_ton_balance
        return await get_ton_balance(address)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════════════════════
#  V12: SUI (6eme reseau, non-EVM)
# ══════════════════════════════════════════════════════════

@router.get("/api/sui/info")
async def sui_info():
    """Infos reseau SUI."""
    from config import SUI_RPC, TREASURY_ADDRESS_SUI, SUI_USDC_TYPE
    return {
        "network": "sui-mainnet",
        "rpc": SUI_RPC,
        "usdc_type": SUI_USDC_TYPE,
        "treasury": TREASURY_ADDRESS_SUI or "not configured",
        "status": "active" if TREASURY_ADDRESS_SUI else "not_configured",
        "supported_currencies": ["SUI", "USDC"],
        "settlement_time": "2-3 seconds",
        "fees": "< $0.01",
    }


@router.post("/api/sui/verify")
async def sui_verify(request: Request):
    """Verifie une transaction sur SUI."""
    await check_rate_limit(request)
    body = await request.json()
    tx_digest = body.get("tx_digest", "") or body.get("tx_hash", "")
    if not tx_digest:
        raise HTTPException(400, "tx_digest (or tx_hash) required")
    try:
        from sui_verifier import verify_sui_transaction
        return await verify_sui_transaction(
            tx_digest,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}


@router.get("/api/sui/balance/{address}")
async def sui_balance(address: str):
    """Solde SUI d'un wallet."""
    try:
        from sui_verifier import get_sui_balance
        return await get_sui_balance(address)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════════════════════
#  V12: TRON (10eme reseau, non-EVM)
# ══════════════════════════════════════════════════════════

@router.get("/api/tron/info")
async def tron_info():
    """Infos reseau TRON."""
    from config import TRON_API_URL, TREASURY_ADDRESS_TRON, TRON_USDT_CONTRACT, TRON_USDC_CONTRACT
    return {
        "network": "tron-mainnet",
        "api": TRON_API_URL,
        "usdt_contract": TRON_USDT_CONTRACT,
        "usdc_contract": TRON_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_TRON or "not configured",
        "status": "active" if TREASURY_ADDRESS_TRON else "not_configured",
        "supported_currencies": ["TRX", "USDT", "USDC"],
        "settlement_time": "3-5 seconds",
        "fees": "< $0.01 (Energy/Bandwidth)",
        "note": "TRON uses USDT (TRC-20) as primary stablecoin — largest USDT network by volume",
    }


@router.post("/api/tron/verify")
async def tron_verify(request: Request):
    """Verifie une transaction sur TRON."""
    await check_rate_limit(request)
    body = await request.json()
    tx_id = body.get("tx_id", "") or body.get("tx_hash", "")
    if not tx_id:
        raise HTTPException(400, "tx_id (or tx_hash) required")
    try:
        from tron_verifier import verify_tron_transaction
        return await verify_tron_transaction(
            tx_id,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}


@router.get("/api/tron/balance/{address}")
async def tron_balance(address: str):
    """Solde TRX d'un wallet TRON."""
    try:
        from tron_verifier import get_tron_balance
        return await get_tron_balance(address)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════════════════════
#  V12: NEAR Protocol (12eme blockchain)
# ══════════════════════════════════════════════════════════

@router.get("/api/near/info")
async def near_info():
    """Infos reseau NEAR Protocol."""
    from config import NEAR_RPC, TREASURY_ADDRESS_NEAR, NEAR_USDC_CONTRACT
    return {
        "network": "near-mainnet", "rpc": NEAR_RPC,
        "usdc_contract": NEAR_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_NEAR or "not configured",
        "status": "active", "supported_currencies": ["NEAR", "USDC"],
        "settlement_time": "1-2 seconds", "fees": "< $0.01",
    }

@router.post("/api/near/verify")
async def near_verify(request: Request):
    """Verifie une transaction NEAR."""
    await check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from near_verifier import verify_near_transaction
        return await verify_near_transaction(
            tx_hash, sender_id=body.get("sender_id", ""),
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}

@router.get("/api/near/balance/{account_id}")
async def near_balance(account_id: str):
    """Solde NEAR d'un compte."""
    try:
        from near_verifier import get_near_balance
        return await get_near_balance(account_id)
    except Exception as e:
        return safe_error(e, "operation")

@router.get("/api/near/usdc-balance/{account_id}")
async def near_usdc_balance(account_id: str):
    """Solde USDC d'un compte NEAR."""
    try:
        from near_verifier import get_near_usdc_balance
        return await get_near_usdc_balance(account_id)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════════════════════
#  V12: Aptos (13eme blockchain)
# ══════════════════════════════════════════════════════════

@router.get("/api/aptos/info")
async def aptos_info():
    """Infos reseau Aptos."""
    from config import APTOS_API, TREASURY_ADDRESS_APTOS, APTOS_USDC_TYPE
    return {
        "network": "aptos-mainnet", "api": APTOS_API,
        "usdc_type": APTOS_USDC_TYPE,
        "treasury": TREASURY_ADDRESS_APTOS or "not configured",
        "status": "active", "supported_currencies": ["APT", "USDC"],
        "settlement_time": "< 1 second", "fees": "< $0.01",
    }

@router.post("/api/aptos/verify")
async def aptos_verify(request: Request):
    """Verifie une transaction Aptos."""
    await check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from aptos_verifier import verify_aptos_transaction
        return await verify_aptos_transaction(
            tx_hash, expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}

@router.get("/api/aptos/balance/{address}")
async def aptos_balance(address: str):
    """Solde APT d'un wallet Aptos."""
    try:
        from aptos_verifier import get_aptos_balance
        return await get_aptos_balance(address)
    except Exception as e:
        return safe_error(e, "operation")

@router.get("/api/aptos/usdc-balance/{address}")
async def aptos_usdc_balance(address: str):
    """Solde USDC d'un wallet Aptos."""
    try:
        from aptos_verifier import get_aptos_usdc_balance
        return await get_aptos_usdc_balance(address)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════════════════════
#  V12: SEI (14eme blockchain, EVM)
# ══════════════════════════════════════════════════════════

@router.get("/api/sei/info")
async def sei_info():
    """Infos reseau SEI."""
    from config import SEI_RPC, SEI_CHAIN_ID, SEI_USDC_CONTRACT, TREASURY_ADDRESS_SEI
    return {
        "network": "sei-mainnet", "rpc": SEI_RPC, "chainId": SEI_CHAIN_ID,
        "usdc_contract": SEI_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_SEI or "not configured",
        "status": "active", "supported_currencies": ["SEI", "USDC"],
        "settlement_time": "390ms", "fees": "< $0.001",
    }

@router.post("/api/sei/verify")
async def sei_verify(request: Request):
    """Verifie une transaction SEI (EVM)."""
    await check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from sei_verifier import verify_sei_transaction
        return await verify_sei_transaction(tx_hash, expected_to=body.get("expected_to"))
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}

@router.post("/api/sei/verify-usdc")
async def sei_verify_usdc(request: Request):
    """Verifie un transfert USDC sur SEI."""
    await check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from sei_verifier import verify_usdc_transfer_sei
        return await verify_usdc_transfer_sei(
            tx_hash, expected_amount_raw=int(body.get("expected_amount_raw", 0)),
            expected_recipient=body.get("expected_recipient"),
        )
    except Exception as e:
        return {"verified": False, "error": "An error occurred"}

@router.get("/api/sei/balance/{address}")
async def sei_balance(address: str):
    """Solde SEI d'un wallet."""
    try:
        from sei_verifier import get_sei_balance
        return await get_sei_balance(address)
    except Exception as e:
        return safe_error(e, "operation")
