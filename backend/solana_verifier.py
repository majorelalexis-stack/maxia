"""MAXIA Solana Verifier V12 — Verification complete (destinataire + montant)"""
import os, httpx, asyncio
from config import get_rpc_url, TREASURY_ADDRESS

SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


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

    rpc = get_rpc_url()
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [tx_signature, {"encoding": "jsonParsed",
                                   "maxSupportedTransactionVersion": 0,
                                   "commitment": "confirmed"}]
    }

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(rpc, json=payload)
                data = resp.json()

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
                    or transfer["amount_usdc"] >= expected_amount_usdc * 0.99  # 1% tolerance
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
            print(f"[Verifier] Tentative {attempt+1} echouee: {e}")
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

        # SPL Token transfer / transferChecked
        if ix_type in ("transfer", "transferChecked") and ix.get("program") == "spl-token":
            amount_str = info.get("tokenAmount", {}).get("uiAmountString")
            if amount_str is None:
                # Pour "transfer" simple (pas transferChecked)
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
                "mint": info.get("mint", ""),
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
