"""MAXIA Jupiter Router V11 — Routing reel des swaps via Jupiter V6 API

Permet d'executer de vrais swaps on-chain :
  USDC -> Token (achat action tokenisee)
  Token -> USDC (vente action tokenisee)
Via Jupiter, le plus gros agregateur DEX sur Solana.
"""
import logging
import asyncio, time, json, base64
import httpx
import base58
from nacl.signing import SigningKey
from config import get_rpc_url, ESCROW_PRIVKEY_B58, ESCROW_ADDRESS

JUPITER_QUOTE_API = "https://lite-api.jup.ag/swap/v1"
JUPITER_SWAP_API = "https://lite-api.jup.ag/swap/v1/swap"
JUPITER_TOKENS_API = "https://tokens.jup.ag/tokens"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

print("[Jupiter] Router initialise — Jupiter V6 API")


async def get_quote(input_mint: str, output_mint: str, amount_raw: int,
                     slippage_bps: int = 50) -> dict:
    """Obtient un devis de swap via Jupiter lite-api (gratuit, retry si rate limit)."""
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_raw),
        "slippageBps": slippage_bps,
        "restrictIntermediateTokens": "true",
    }
    jup_urls = [
        "https://lite-api.jup.ag/swap/v1/quote",
        "https://api.jup.ag/swap/v1/quote",
    ]
    last_error = ""
    for jup_url in jup_urls:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(jup_url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        return {
                            "success": True,
                            "inputMint": input_mint,
                            "outputMint": output_mint,
                            "inAmount": data.get("inAmount", "0"),
                            "outAmount": data.get("outAmount", "0"),
                            "priceImpactPct": data.get("priceImpactPct", "0"),
                            "routePlan": [
                                {"swapInfo": step.get("swapInfo", {}).get("label", ""),
                                 "percent": step.get("percent", 100)}
                                for step in data.get("routePlan", [])
                            ],
                            "raw_quote": data,
                        }
                    elif resp.status_code == 429:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    else:
                        last_error = f"Jupiter {resp.status_code}: {resp.text[:100]}"
                        break
            except Exception as e:
                last_error = str(e)
                break
    return {"success": False, "error": last_error or "Jupiter unavailable"}


async def execute_swap(quote_response: dict, user_wallet: str) -> dict:
    """Execute un swap via Jupiter. Retourne la transaction a signer."""
    try:
        body = {
            "quoteResponse": quote_response,
            "userPublicKey": user_wallet,
            "wrapAndUnwrapSol": True,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(JUPITER_SWAP_API, json=body)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "swapTransaction": data.get("swapTransaction", ""),
                    "lastValidBlockHeight": data.get("lastValidBlockHeight", 0),
                }
            else:
                return {"success": False, "error": f"Jupiter swap error: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


async def sign_and_send_swap(swap_transaction_b64: str) -> dict:
    """Signe et envoie la transaction de swap (via le wallet escrow)."""
    if not ESCROW_PRIVKEY_B58:
        return {"success": False, "error": "Escrow wallet non configure"}

    try:
        rpc = get_rpc_url()
        tx_bytes = base64.b64decode(swap_transaction_b64)

        # Fix #2: Validate transaction length before slicing
        if len(tx_bytes) < 65:
            return {"success": False, "error": "Invalid transaction format from Jupiter"}

        # Signer la transaction
        secret = base58.b58decode(ESCROW_PRIVKEY_B58)
        signing_key = SigningKey(secret[:32])

        # La transaction Jupiter est deja serialisee, il faut la signer
        # Le message a signer commence apres les signatures existantes
        # Format: num_signatures (1 byte) + signatures (64 * num) + message
        num_sigs = tx_bytes[0]
        msg_start = 1 + (num_sigs * 64)
        if msg_start >= len(tx_bytes):
            return {"success": False, "error": "Invalid transaction format: message offset out of bounds"}
        message = tx_bytes[msg_start:]

        signature = signing_key.sign(message).signature

        # Remplacer la premiere signature (qui est vide/placeholder)
        signed_tx = bytes([num_sigs]) + signature + tx_bytes[65:]

        # Envoyer
        tx_b64 = base64.b64encode(signed_tx).decode("ascii")
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [tx_b64, {"encoding": "base64", "skipPreflight": True}],
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(rpc, json=payload)
            data = resp.json()

        if "result" in data:
            sig = data["result"]
            print(f"[Jupiter] Swap TX sent: {sig[:20]}...")
            return {
                "success": True,
                "signature": sig,
                "explorer": f"https://solscan.io/tx/{sig}",
            }
        else:
            error = data.get("error", {}).get("message", str(data))
            return {"success": False, "error": error}

    except Exception as e:
        return {"success": False, "error": "An error occurred"}


async def buy_token_via_jupiter(token_mint: str, amount_usdc: float,
                                  buyer_wallet: str) -> dict:
    """Achete un token en routant via Jupiter (USDC -> Token).

    WARNING (#8): Using escrow wallet for Jupiter swaps.
    In production, each user should sign their own transactions.
    Current architecture: MAXIA acts as intermediary (receives USDC,
    swaps via escrow, sends tokens to buyer).
    """
    # #8: Safety cap — prevent excessively large swaps through escrow
    MAX_SWAP_AMOUNT_USD = 10000
    if amount_usdc > MAX_SWAP_AMOUNT_USD:
        return {"success": False, "error": f"Swap amount exceeds safety limit (${MAX_SWAP_AMOUNT_USD})"}

    amount_raw = int(amount_usdc * 1e6)  # USDC a 6 decimales

    # 1. Obtenir le devis
    quote = await get_quote(USDC_MINT, token_mint, amount_raw)
    if not quote.get("success"):
        return quote

    # 2. Obtenir la transaction de swap
    swap = await execute_swap(quote["raw_quote"], buyer_wallet)
    if not swap.get("success"):
        return swap

    # 3. Signer et envoyer
    result = await sign_and_send_swap(swap["swapTransaction"])
    if not result.get("success"):
        return result

    return {
        "success": True,
        "signature": result["signature"],
        "explorer": result["explorer"],
        "input": f"{amount_usdc} USDC",
        "output_amount": quote.get("outAmount", "0"),
        "output_mint": token_mint,
        "price_impact": quote.get("priceImpactPct", "0"),
        "route": [r["swapInfo"] for r in quote.get("routePlan", [])],
    }


async def sell_token_via_jupiter(token_mint: str, amount_tokens_raw: int,
                                   seller_wallet: str) -> dict:
    """Vend un token via Jupiter (Token -> USDC)."""
    # 1. Obtenir le devis
    quote = await get_quote(token_mint, USDC_MINT, amount_tokens_raw)
    if not quote.get("success"):
        return quote

    # 2. Obtenir la transaction
    swap = await execute_swap(quote["raw_quote"], seller_wallet)
    if not swap.get("success"):
        return swap

    # 3. Signer et envoyer
    result = await sign_and_send_swap(swap["swapTransaction"])
    if not result.get("success"):
        return result

    return {
        "success": True,
        "signature": result["signature"],
        "explorer": result["explorer"],
        "input_tokens": amount_tokens_raw,
        "input_mint": token_mint,
        "output_usdc": int(quote.get("outAmount", "0")) / 1e6,
        "price_impact": quote.get("priceImpactPct", "0"),
    }


async def get_token_price_jupiter(token_mint: str) -> float:
    """Obtient le prix d'un token en USDC via Jupiter quote."""
    try:
        # Demander le prix pour 1 USDC worth
        quote = await get_quote(USDC_MINT, token_mint, 1_000_000)  # 1 USDC
        if quote.get("success"):
            out_amount = int(quote.get("outAmount", "0"))
            if out_amount > 0:
                # Prix = 1 / (tokens recus pour 1 USDC)
                return 1.0 / (out_amount / 1e6) if out_amount > 0 else 0
        return 0
    except Exception:
        return 0
