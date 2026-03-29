"""MAXIA Solana Verifier V12 — Verification complete (destinataire + montant)
RPC failover : essaie chaque URL dans l'ordre (Helius > custom > publics)."""
import logging
import os, httpx, asyncio
from config import get_rpc_url, TREASURY_ADDRESS, SOLANA_RPC_URLS

logger = logging.getLogger(__name__)

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


async def _rpc_post(payload: dict, timeout: float = 8) -> dict:
    """Post RPC avec failover sur toutes les URLs Solana.
    Meme pattern que base_verifier._rpc_post."""
    last_error = None
    for rpc_url in SOLANA_RPC_URLS:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(rpc_url, json=payload)
                data = resp.json()
            if "error" in data and data["error"]:
                last_error = Exception(f"RPC error: {data['error']}")
                continue
            return data
        except httpx.TimeoutException as e:
            last_error = e
        except httpx.ConnectError as e:
            last_error = e
        except Exception as e:
            last_error = e
    raise last_error or Exception("All Solana RPC endpoints failed")


async def verify_transaction(tx_signature: str, expected_wallet: str = None,
                              expected_amount_usdc: float = 0,
                              expected_recipient: str = None) -> dict:
    """
    Verifie une transaction Solana on-chain.
    Retourne un dict avec:
      - valid: bool
      - amount_usdc: float (montant USDC transfere)
      - from: str (expediteur)
      - to: str (destinataire)
      - error: str (si echec)
    """
    if not expected_recipient:
        expected_recipient = TREASURY_ADDRESS

    # Global timeout — never hang more than 20s total
    try:
        return await asyncio.wait_for(
            _verify_transaction_inner(tx_signature, expected_wallet, expected_amount_usdc, expected_recipient),
            timeout=20
        )
    except asyncio.TimeoutError:
        return {"valid": False, "error": "Transaction verification timed out (20s). Solana RPC may be slow. Try again."}


async def _verify_transaction_inner(tx_signature: str, expected_wallet: str,
                                      expected_amount_usdc: float, expected_recipient: str) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [tx_signature, {"encoding": "jsonParsed",
                                   "maxSupportedTransactionVersion": 0,
                                   "commitment": "finalized"}]
    }

    for attempt in range(2):  # Max 2 attempts (was 3) — faster response
        try:
            data = await _rpc_post(payload)

            result = data.get("result")
            if not result:
                await asyncio.sleep(2 ** attempt)
                continue

            meta = result.get("meta", {})
            if meta.get("err") is not None:
                return {"valid": False, "error": f"Transaction echouee: {meta['err']}"}

            # Parser les instructions pour trouver les transferts
            tx_info = _parse_transfers(result)

            if not tx_info["transfers"]:
                return {"valid": False, "error": "Aucun transfert trouve dans la transaction"}

            # Chercher un transfert USDC vers le destinataire attendu
            for transfer in tx_info["transfers"]:
                recipient_match = (
                    not expected_recipient
                    or transfer["to"].lower() == expected_recipient.lower()
                )
                amount_match = (
                    expected_amount_usdc <= 0
                    or transfer["amount_usdc"] >= expected_amount_usdc * 0.999  # 0.1% tolerance (V-06)
                )

                if recipient_match and amount_match:
                    return {
                        "valid": True,
                        "signature": tx_signature,
                        "from": transfer["from"],
                        "to": transfer["to"],
                        "amount_usdc": transfer["amount_usdc"],
                        "amount_raw": transfer["amount_raw"],
                        "token": transfer.get("mint", "SOL"),
                    }

            # Aucun transfert ne correspond
            if expected_recipient and expected_amount_usdc > 0:
                return {
                    "valid": False,
                    "error": f"Aucun transfert de {expected_amount_usdc} USDC vers {expected_recipient[:12]}... trouve",
                    "transfers_found": tx_info["transfers"],
                }
            elif expected_recipient:
                return {
                    "valid": False,
                    "error": f"Aucun transfert vers {expected_recipient[:12]}... trouve",
                    "transfers_found": tx_info["transfers"],
                }
            else:
                return {
                    "valid": False,
                    "error": f"Aucun transfert de {expected_amount_usdc} USDC trouve",
                    "transfers_found": tx_info["transfers"],
                }

        except Exception as e:
            logger.error(f"Tentative {attempt+1} echouee: {e}")
            await asyncio.sleep(2 ** attempt)

    return {"valid": False, "error": "Verification echouee apres 3 tentatives"}


def _parse_transfers(result: dict) -> dict:
    """Parse les transferts SOL et SPL Token d'une transaction."""
    transfers = []
    tx = result.get("transaction", {})
    message = tx.get("message", {})
    instructions = message.get("instructions", [])
    inner_instructions = result.get("meta", {}).get("innerInstructions", [])

    # Chercher dans les instructions principales + inner
    all_instructions = list(instructions)
    for inner in inner_instructions:
        all_instructions.extend(inner.get("instructions", []))

    for ix in all_instructions:
        parsed = ix.get("parsed")
        if not parsed:
            continue

        ix_type = parsed.get("type", "")
        info = parsed.get("info", {})

        # SPL Token transfer / transferChecked — USDC ONLY
        if ix_type in ("transfer", "transferChecked") and ix.get("program") == "spl-token":
            # V-05: Verify token mint is USDC (reject worthless tokens)
            token_mint = info.get("mint", "")
            if ix_type == "transferChecked":
                # transferChecked always has mint — reject if not USDC
                if token_mint and token_mint != USDC_MINT:
                    continue
            elif ix_type == "transfer":
                # Plain transfer has no mint field — REJECT (cannot verify it's USDC)
                # An attacker could send a worthless SPL token via plain transfer
                if not token_mint or token_mint != USDC_MINT:
                    continue

            amount_str = info.get("tokenAmount", {}).get("uiAmountString")
            if amount_str is None:
                amount_raw = int(info.get("amount", 0))
                amount_usdc = amount_raw / 1e6
            else:
                amount_usdc = float(amount_str)
                amount_raw = int(info.get("tokenAmount", {}).get("amount", 0))

            transfers.append({
                "type": "spl_token",
                "from": info.get("authority", info.get("source", "")),
                "to": info.get("destination", ""),
                "amount_usdc": amount_usdc,
                "amount_raw": amount_raw,
                "mint": token_mint,
            })

        # SOL transfer
        elif ix_type == "transfer" and ix.get("program") == "system":
            lamports = int(info.get("lamports", 0))
            transfers.append({
                "type": "sol",
                "from": info.get("source", ""),
                "to": info.get("destination", ""),
                "amount_usdc": 0,  # SOL, pas USDC
                "amount_raw": lamports,
                "mint": "SOL",
            })

    # Aussi verifier les pre/postTokenBalances pour trouver le owner des token accounts
    pre_balances = result.get("meta", {}).get("preTokenBalances", [])
    post_balances = result.get("meta", {}).get("postTokenBalances", [])
    account_keys = message.get("accountKeys", [])

    # Map token account -> owner pour resoudre les addresses
    token_account_owners = {}
    for bal in pre_balances + post_balances:
        acc_index = bal.get("accountIndex", -1)
        owner = bal.get("owner", "")
        if 0 <= acc_index < len(account_keys):
            key = account_keys[acc_index]
            if isinstance(key, dict):
                key = key.get("pubkey", "")
            token_account_owners[key] = owner

    # Resoudre les token accounts vers les wallets owners
    for transfer in transfers:
        if transfer["type"] == "spl_token":
            if transfer["from"] in token_account_owners:
                transfer["from"] = token_account_owners[transfer["from"]]
            if transfer["to"] in token_account_owners:
                transfer["to"] = token_account_owners[transfer["to"]]

    return {"transfers": transfers}
