"""MAXIA Currency Converter — Multi-currency support (ETH/SOL native payments)"""
import os, time, asyncio
import httpx
from config import get_rpc_url, ETH_RPC
from http_client import get_http_client

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=solana,ethereum&vs_currencies=usd"

# In-memory price cache with TTL
_price_cache = {
    "SOL": {"price": 0.0, "updated_at": 0},
    "ETH": {"price": 0.0, "updated_at": 0},
}
_CACHE_TTL_S = 30


async def _fetch_prices() -> dict:
    """Fetch live SOL/USD and ETH/USD from CoinGecko. Handles rate limits gracefully."""
    try:
        client = get_http_client()
        resp = await client.get(COINGECKO_URL, timeout=10)
        if resp.status_code == 429:
            print("[CurrencyConverter] CoinGecko rate limit — using cached prices")
            return {}
        resp.raise_for_status()
        data = resp.json()
        prices = {}
        if "solana" in data and "usd" in data["solana"]:
            prices["SOL"] = float(data["solana"]["usd"])
        if "ethereum" in data and "usd" in data["ethereum"]:
            prices["ETH"] = float(data["ethereum"]["usd"])
        return prices
    except Exception as e:
        print(f"[CurrencyConverter] Erreur fetch prix: {e}")
        return {}


async def get_price(currency: str) -> float:
    """Returns USD price for SOL or ETH (cached 30s). Returns 0 if unavailable."""
    currency = currency.upper()
    if currency not in ("SOL", "ETH"):
        return 0.0

    cached = _price_cache.get(currency, {})
    now = time.time()

    # Return cached if fresh
    if cached.get("price", 0) > 0 and (now - cached.get("updated_at", 0)) < _CACHE_TTL_S:
        return cached["price"]

    # Fetch fresh prices
    prices = await _fetch_prices()
    for sym, price in prices.items():
        _price_cache[sym] = {"price": price, "updated_at": now}

    # Return new price or fallback to last known
    if currency in prices:
        return prices[currency]
    return cached.get("price", 0.0)


async def get_usdc_equivalent(amount: float, currency: str) -> float:
    """Converts amount in currency to USDC equivalent. USDC = 1:1."""
    currency = currency.upper()
    if currency == "USDC":
        return amount
    price = await get_price(currency)
    if price <= 0:
        return 0.0
    return round(amount * price, 6)


async def verify_native_sol_payment(tx_signature: str, expected_usdc: float,
                                     slippage_pct: float = 2.0) -> dict:
    """
    Verify a native SOL transfer is worth >= expected_usdc * (1 - slippage).
    Uses solana_verifier to get the TX, then checks SOL amount * SOL price.
    """
    if not tx_signature:
        return {"valid": False, "error": "tx_signature requis"}

    rpc = get_rpc_url()
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [tx_signature, {"encoding": "jsonParsed",
                                   "maxSupportedTransactionVersion": 0,
                                   "commitment": "confirmed"}],
    }

    for attempt in range(3):
        try:
            client = get_http_client()
            resp = await client.post(rpc, json=payload, timeout=15)
            data = resp.json()

            result = data.get("result")
            if not result:
                await asyncio.sleep(2 ** attempt)
                continue

            meta = result.get("meta", {})
            if meta.get("err") is not None:
                return {"valid": False, "error": f"Transaction echouee: {meta['err']}"}

            # Find SOL transfers in instructions
            tx = result.get("transaction", {})
            message = tx.get("message", {})
            instructions = message.get("instructions", [])
            inner_instructions = meta.get("innerInstructions", [])

            all_ix = list(instructions)
            for inner in inner_instructions:
                all_ix.extend(inner.get("instructions", []))

            sol_transferred = 0.0
            sender = ""
            recipient = ""

            for ix in all_ix:
                parsed = ix.get("parsed")
                if not parsed:
                    continue
                if parsed.get("type") == "transfer" and ix.get("program") == "system":
                    lamports = int(parsed.get("info", {}).get("lamports", 0))
                    sol_amount = lamports / 1e9
                    if sol_amount > sol_transferred:
                        sol_transferred = sol_amount
                        sender = parsed.get("info", {}).get("source", "")
                        recipient = parsed.get("info", {}).get("destination", "")

            if sol_transferred <= 0:
                return {"valid": False, "error": "Aucun transfert SOL natif trouve"}

            # Get SOL price and compute USDC value
            sol_price = await get_price("SOL")
            if sol_price <= 0:
                return {"valid": False, "error": "Prix SOL indisponible"}

            usdc_value = sol_transferred * sol_price
            min_required = expected_usdc * (1 - slippage_pct / 100)

            if usdc_value >= min_required:
                return {
                    "valid": True,
                    "signature": tx_signature,
                    "currency": "SOL",
                    "amount_sol": sol_transferred,
                    "sol_price_usd": sol_price,
                    "usdc_value": round(usdc_value, 6),
                    "expected_usdc": expected_usdc,
                    "from": sender,
                    "to": recipient,
                }
            else:
                return {
                    "valid": False,
                    "error": f"Montant insuffisant: {usdc_value:.2f} USDC < {min_required:.2f} USDC (slippage {slippage_pct}%)",
                    "amount_sol": sol_transferred,
                    "usdc_value": round(usdc_value, 6),
                }

        except Exception as e:
            print(f"[CurrencyConverter] SOL verify attempt {attempt + 1}: {e}")
            await asyncio.sleep(2 ** attempt)

    return {"valid": False, "error": "Verification echouee apres 3 tentatives"}


async def verify_native_eth_payment(tx_hash: str, expected_usdc: float,
                                     slippage_pct: float = 2.0) -> dict:
    """
    Verify a native ETH transfer is worth >= expected_usdc * (1 - slippage).
    Uses eth_getTransaction to check ETH value * ETH price.
    """
    if not tx_hash:
        return {"valid": False, "error": "tx_hash requis"}

    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransaction",
        "params": [tx_hash],
    }

    for attempt in range(3):
        try:
            client = get_http_client()
            resp = await client.post(ETH_RPC, json=payload, timeout=20)
            data = resp.json()

            result = data.get("result")
            if not result:
                await asyncio.sleep(2 ** attempt)
                continue

            # Parse ETH value from transaction
            value_hex = result.get("value", "0x0")
            value_wei = int(value_hex, 16)
            eth_amount = value_wei / 1e18

            if eth_amount <= 0:
                return {"valid": False, "error": "Aucun ETH natif transfere (value=0)"}

            # Verify transaction was confirmed
            receipt_payload = {
                "jsonrpc": "2.0", "id": 2,
                "method": "eth_getTransactionReceipt",
                "params": [tx_hash],
            }
            client = get_http_client()
            receipt_resp = await client.post(ETH_RPC, json=receipt_payload, timeout=20)
            receipt_data = receipt_resp.json()

            receipt = receipt_data.get("result")
            if not receipt:
                return {"valid": False, "error": "Transaction non confirmee"}
            if receipt.get("status") != "0x1":
                return {"valid": False, "error": "Transaction reverted"}

            # Get ETH price and compute USDC value
            eth_price = await get_price("ETH")
            if eth_price <= 0:
                return {"valid": False, "error": "Prix ETH indisponible"}

            usdc_value = eth_amount * eth_price
            min_required = expected_usdc * (1 - slippage_pct / 100)

            if usdc_value >= min_required:
                return {
                    "valid": True,
                    "tx_hash": tx_hash,
                    "currency": "ETH",
                    "amount_eth": eth_amount,
                    "eth_price_usd": eth_price,
                    "usdc_value": round(usdc_value, 6),
                    "expected_usdc": expected_usdc,
                    "from": result.get("from", ""),
                    "to": result.get("to", ""),
                    "network": "ethereum-mainnet",
                }
            else:
                return {
                    "valid": False,
                    "error": f"Montant insuffisant: {usdc_value:.2f} USDC < {min_required:.2f} USDC (slippage {slippage_pct}%)",
                    "amount_eth": eth_amount,
                    "usdc_value": round(usdc_value, 6),
                }

        except Exception as e:
            print(f"[CurrencyConverter] ETH verify attempt {attempt + 1}: {e}")
            await asyncio.sleep(2 ** attempt)

    return {"valid": False, "error": "Verification echouee apres 3 tentatives"}


async def verify_payment(tx_sig: str, expected_usdc: float,
                          currency: str, chain: str = "auto") -> dict:
    """
    Unified payment verifier — routes to the right verifier based on currency + chain.
    Supported: USDC (Solana/Base/ETH), SOL (native), ETH (native).
    """
    currency = currency.upper()
    chain = chain.lower()

    # USDC payments — use existing verifiers
    if currency == "USDC":
        if chain in ("solana", "auto"):
            from solana_verifier import verify_transaction
            return await verify_transaction(
                tx_signature=tx_sig,
                expected_amount_usdc=expected_usdc,
            )
        elif chain == "base":
            from base_verifier import verify_usdc_transfer_base
            return await verify_usdc_transfer_base(
                tx_hash=tx_sig,
                expected_amount_raw=int(expected_usdc * 1e6),
            )
        elif chain in ("ethereum", "eth"):
            from eth_verifier import verify_usdc_transfer_eth
            return await verify_usdc_transfer_eth(
                tx_hash=tx_sig,
                expected_amount_raw=int(expected_usdc * 1e6),
            )
        else:
            return {"valid": False, "error": f"Chain non supportee pour USDC: {chain}"}

    # Native SOL
    elif currency == "SOL":
        return await verify_native_sol_payment(tx_sig, expected_usdc)

    # Native ETH
    elif currency == "ETH":
        return await verify_native_eth_payment(tx_sig, expected_usdc)

    else:
        return {"valid": False, "error": f"Currency non supportee: {currency}"}
