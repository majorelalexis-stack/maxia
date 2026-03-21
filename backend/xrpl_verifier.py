"""MAXIA Art.X — XRP Ledger verification + USDC support (3eme blockchain)

Verifie les transactions XRP et USDC sur XRPL mainnet.
Pattern identique a solana_verifier.py et base_verifier.py.
"""
import asyncio
from datetime import datetime


# XRPL USDC issuer (Circle)
XRPL_USDC_ISSUER = "rcEGREd8NmkKRE8GE424sksyt1tJVFZwu"
# On XRPL, standard currency codes are 3 characters (ISO 4217).
# Circle's USDC on XRPL uses "USD" as the currency code (not "USDC").
# This is correct per XRPL spec: 3-char codes are native, longer codes are hex-encoded.
XRPL_USDC_CURRENCY = "USD"


def _get_client():
    """Retourne un client XRPL JSON-RPC (synchrone, wrap en async)."""
    from xrpl.clients import JsonRpcClient
    import os
    rpc_url = os.getenv("XRPL_RPC", "https://s2.ripple.com:51234/")
    return JsonRpcClient(rpc_url)


async def verify_xrpl_transaction(tx_hash: str, expected_dest: str = "",
                                   expected_amount: float = 0,
                                   currency: str = "XRP") -> dict:
    """Verifie une transaction sur XRPL.

    Returns: {verified: bool, amount, sender, receiver, currency, error?}
    """
    try:
        from xrpl.models.requests import Tx
        client = _get_client()

        def _verify():
            resp = client.request(Tx(transaction=tx_hash))
            result = resp.result
            if not result.get("validated"):
                return {"verified": False, "error": "Transaction pas encore validee"}

            meta = result.get("meta", {})
            tx_result = meta.get("TransactionResult", "")
            if tx_result != "tesSUCCESS":
                return {"verified": False, "error": f"Transaction echouee: {tx_result}"}

            tx_type = result.get("TransactionType", "")
            if tx_type != "Payment":
                return {"verified": False, "error": f"Pas un Payment: {tx_type}"}

            sender = result.get("Account", "")
            receiver = result.get("Destination", "")

            # Determiner le montant
            amount_raw = result.get("Amount", {})
            if isinstance(amount_raw, str):
                # XRP natif (en drops)
                amount = int(amount_raw) / 1_000_000
                tx_currency = "XRP"
            elif isinstance(amount_raw, dict):
                # Token IOU (USDC, etc.)
                amount = float(amount_raw.get("value", 0))
                tx_currency = amount_raw.get("currency", "")
                issuer = amount_raw.get("issuer", "")
            else:
                return {"verified": False, "error": "Format montant inconnu"}

            # Verifications
            # XRP addresses are case-sensitive (base58check) — exact comparison required
            if expected_dest and receiver != expected_dest:
                return {"verified": False, "error": f"Destinataire incorrect: {receiver}"}
            if expected_amount > 0 and amount < expected_amount * 0.99:
                return {"verified": False, "error": f"Montant insuffisant: {amount} < {expected_amount}"}

            return {
                "verified": True,
                "tx_hash": tx_hash,
                "sender": sender,
                "receiver": receiver,
                "amount": amount,
                "currency": tx_currency,
                "timestamp": result.get("date", 0),
                "ledger_index": result.get("ledger_index", 0),
            }

        return await asyncio.to_thread(_verify)

    except ImportError:
        return {"verified": False, "error": "xrpl-py not installed (pip install xrpl-py)"}
    except Exception as e:
        return {"verified": False, "error": str(e)}


async def get_xrpl_balance(address: str) -> dict:
    """Recupere le solde XRP + tokens d'un wallet XRPL."""
    try:
        from xrpl.account import get_balance
        from xrpl.models.requests import AccountLines
        client = _get_client()

        def _balance():
            # Solde XRP
            try:
                xrp_drops = get_balance(address, client)
                xrp = xrp_drops / 1_000_000
            except Exception:
                xrp = 0

            # Tokens (trustlines) — USDC etc.
            tokens = []
            try:
                resp = client.request(AccountLines(account=address))
                for line in resp.result.get("lines", []):
                    tokens.append({
                        "currency": line.get("currency", ""),
                        "balance": float(line.get("balance", 0)),
                        "issuer": line.get("account", ""),
                    })
            except Exception:
                pass

            # Chercher USDC specifiquement
            usdc = 0
            for t in tokens:
                if t["currency"] == XRPL_USDC_CURRENCY and t["issuer"] == XRPL_USDC_ISSUER:
                    usdc = t["balance"]

            return {
                "address": address,
                "xrp": xrp,
                "usdc": usdc,
                "tokens": tokens,
            }

        return await asyncio.to_thread(_balance)

    except ImportError:
        return {"address": address, "error": "xrpl-py not installed"}
    except Exception as e:
        return {"address": address, "error": str(e)}


async def verify_usdc_transfer_xrpl(tx_hash: str, expected_dest: str,
                                     min_amount: float) -> dict:
    """Verifie specifiquement un transfert USDC sur XRPL."""
    result = await verify_xrpl_transaction(tx_hash, expected_dest, min_amount, "USD")
    if result.get("verified") and result.get("currency") != XRPL_USDC_CURRENCY:
        return {"verified": False, "error": f"Pas un transfert USDC: {result.get('currency')}"}
    return result
