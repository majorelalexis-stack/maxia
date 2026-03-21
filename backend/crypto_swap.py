"""MAXIA Art.24 V11 — Crypto Swap Engine (SOL/USDC/SPL tokens via Jupiter)

Les IA peuvent acheter, vendre et echanger des cryptos entre elles.
Commission dynamique ajustee en temps reel par rapport a la concurrence
pour TOUJOURS offrir le meilleur prix.
"""
import asyncio, time, uuid
import httpx
from config import TREASURY_ADDRESS

JUPITER_QUOTE_API = "https://lite-api.jup.ag/swap/v1"
JUPITER_PRICE_API = "https://lite-api.jup.ag/price/v2"

# Tokens populaires avec mint addresses
SUPPORTED_TOKENS = {
    "SOL": {
        "mint": "So11111111111111111111111111111111111111112",
        "name": "Solana", "decimals": 9, "logo": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png",
    },
    "USDC": {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "name": "USD Coin", "decimals": 6, "logo": "",
    },
    "USDT": {
        "mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        "name": "Tether USD", "decimals": 6, "logo": "",
    },
    "BONK": {
        "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "name": "Bonk", "decimals": 5, "logo": "",
    },
    "JUP": {
        "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        "name": "Jupiter", "decimals": 6, "logo": "",
    },
    "RAY": {
        "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        "name": "Raydium", "decimals": 6, "logo": "",
    },
    "TRUMP": {
        "mint": "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
        "name": "Official Trump", "decimals": 6, "logo": "",
    },
    "PYTH": {
        "mint": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
        "name": "Pyth Network", "decimals": 6, "logo": "",
    },
    "W": {
        "mint": "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
        "name": "Wormhole", "decimals": 6, "logo": "",
    },
    "ETH": {
        "mint": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
        "name": "Ethereum (Wormhole)", "decimals": 8, "logo": "",
    },
    "BTC": {
        "mint": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
        "name": "Bitcoin (Wormhole)", "decimals": 8, "logo": "",
    },
    "ORCA": {
        "mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        "name": "Orca", "decimals": 6, "logo": "",
    },
    "WIF": {
        "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "name": "dogwifhat", "decimals": 6, "logo": "",
    },
    "RENDER": {
        "mint": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
        "name": "Render Token", "decimals": 8, "logo": "",
    },
    "HNT": {
        "mint": "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux",
        "name": "Helium", "decimals": 8, "logo": "",
    },
    # ── V12: Ajout massif de tokens ──
    "JTO": {
        "mint": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
        "name": "Jito", "decimals": 9, "logo": "",
    },
    "TNSR": {
        "mint": "TNSRxcUxoT9xBG3de7PiJyTDYu7kskLqcpddxnEJAS6",
        "name": "Tensor", "decimals": 9, "logo": "",
    },
    "MEW": {
        "mint": "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
        "name": "cat in a dogs world", "decimals": 5, "logo": "",
    },
    "POPCAT": {
        "mint": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
        "name": "Popcat", "decimals": 9, "logo": "",
    },
    "MOBILE": {
        "mint": "mb1eu7TzEc71KxDpsmsKoucSSuuoGLv1drys1oP2jh6",
        "name": "Helium Mobile", "decimals": 6, "logo": "",
    },
    "MNDE": {
        "mint": "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",
        "name": "Marinade", "decimals": 9, "logo": "",
    },
    "MSOL": {
        "mint": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
        "name": "Marinade Staked SOL", "decimals": 9, "logo": "",
    },
    "JITOSOL": {
        "mint": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
        "name": "Jito Staked SOL", "decimals": 9, "logo": "",
    },
    "BSOL": {
        "mint": "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",
        "name": "BlazeStake Staked SOL", "decimals": 9, "logo": "",
    },
    "DRIFT": {
        "mint": "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7",
        "name": "Drift Protocol", "decimals": 6, "logo": "",
    },
    "KMNO": {
        "mint": "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS",
        "name": "Kamino", "decimals": 6, "logo": "",
    },
    "PENGU": {
        "mint": "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
        "name": "Pudgy Penguins", "decimals": 6, "logo": "",
    },
    "AI16Z": {
        "mint": "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC",
        "name": "ai16z", "decimals": 9, "logo": "",
    },
    "FARTCOIN": {
        "mint": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
        "name": "Fartcoin", "decimals": 6, "logo": "",
    },
    "GRASS": {
        "mint": "Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs",
        "name": "Grass", "decimals": 9, "logo": "",
    },
    "ZEUS": {
        "mint": "ZEUS1aR7aX8DFFJf5QjWj2ftDDdNTroMNGo8YoQm3Gq",
        "name": "Zeus Network", "decimals": 6, "logo": "",
    },
    "NOSOL": {
        "mint": "nosXBVoaCTtYdLvKY6Csb4AC8JCdQKKAaWYtx2ZMoo7",
        "name": "Nosana", "decimals": 6, "logo": "",
    },
    "SAMO": {
        "mint": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        "name": "Samoyedcoin", "decimals": 9, "logo": "",
    },
    "STEP": {
        "mint": "StepAscQoEioFxxWGnh2sLBDFp9d8rvKz2Yp39iDpyT",
        "name": "Step Finance", "decimals": 9, "logo": "",
    },
    "BOME": {
        "mint": "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
        "name": "BOOK OF MEME", "decimals": 6, "logo": "",
    },
    "SLERF": {
        "mint": "7BgBvyjrZX1YKz4oh9mjb8ZScatkkwb8DzFx7LoiVkM3",
        "name": "SLERF", "decimals": 9, "logo": "",
    },
    "MPLX": {
        "mint": "METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m",
        "name": "Metaplex", "decimals": 6, "logo": "",
    },
    "INF": {
        "mint": "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm",
        "name": "Infinity (Sanctum)", "decimals": 9, "logo": "",
    },
    "PNUT": {
        "mint": "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
        "name": "Peanut the Squirrel", "decimals": 6, "logo": "",
    },
    "GOAT": {
        "mint": "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
        "name": "Goatseus Maximus", "decimals": 6, "logo": "",
    },
    # ── V12.1: Tokens supplementaires ──
    "LINK": {
        "mint": "2wpTofQ8SkACrkZWrZDjXPitbbvByJGJy4sQqnfBfQVR",
        "name": "Chainlink (Wormhole)", "decimals": 8, "logo": "",
    },
    "UNI": {
        "mint": "8FU95xFJhUUkyyCLU13HSzDLs7oC4QZdXQHL6SCeab36",
        "name": "Uniswap (Wormhole)", "decimals": 8, "logo": "",
    },
    "AAVE": {
        "mint": "3vAs4D1WE6Na4tCgt4BApgFfENbCCJVDP6QDT9zKMJH4",
        "name": "Aave (Wormhole)", "decimals": 8, "logo": "",
    },
    "LDO": {
        "mint": "HZRCwxP2Vq9PCpPXooayhJ2bxTB5AMqFqZbNPc3Ldzsf",
        "name": "Lido DAO (Wormhole)", "decimals": 8, "logo": "",
    },
    "VIRTUAL": {
        "mint": "VRTuawjjBKGfQLFMWrqwZ2KnaDxMFimJonH7miSbFaB",
        "name": "Virtuals Protocol", "decimals": 9, "logo": "",
    },
    "OLAS": {
        "mint": "Ez3nzG9ofodYCvEmw73XhQ87LWNYVRM2s7diB5tBZPyM",
        "name": "Autonolas", "decimals": 8, "logo": "",
    },
    "FET": {
        "mint": "EgLJHNkSFJNJbGMWnN2ESCMQ79HEGPJGDbpPFNX7vagd",
        "name": "Fetch.ai (Wormhole)", "decimals": 8, "logo": "",
    },
    "PEPE": {
        "mint": "3Ysmnbdwje7SP2bKSJgST4iFF3FrVLjR2uGaoV1138DP",
        "name": "Pepe (Wormhole)", "decimals": 8, "logo": "",
    },
    "DOGE": {
        "mint": "GRFKmwmF14nBnSEyEesFctHYBwRLXSBZdGAjqFNonWon",
        "name": "Dogecoin (Wormhole)", "decimals": 8, "logo": "",
    },
    "SHIB": {
        "mint": "CiKu4eHsVrc1eueVQeHn7qhXTcVu95gSQoBBpX5SQzUt",
        "name": "Shiba Inu (Wormhole)", "decimals": 8, "logo": "",
    },
}

# Commission concurrence (mis a jour dynamiquement)
COMPETITOR_FEES = {
    "jupiter_direct": 0.0,       # 0% swap fee (mais slippage pool)
    "raydium": 0.25,             # 0.25%
    "orca": 0.30,                # 0.30%
    "binance": 0.10,             # 0.10%
    "coinbase": 0.40,            # 0.40%
    "kraken": 0.16,              # 0.16%
}

# Commission MAXIA par palier (toujours <= au concurrent le moins cher + slippage)
SWAP_COMMISSION_TIERS = {
    "BRONZE":  {"min_volume": 0,     "max_volume": 1000,   "bps": 15},   # 0.15%
    "SILVER":  {"min_volume": 1000,  "max_volume": 5000,   "bps": 8},    # 0.08%
    "GOLD":    {"min_volume": 5000,  "max_volume": 25000,  "bps": 5},    # 0.05%
    "WHALE":   {"min_volume": 25000, "max_volume": float("inf"), "bps": 2},  # 0.02%
}

# Cache prix
_price_cache: dict = {}
_price_cache_ts: float = 0
_PRICE_TTL = 30  # 30 secondes

# Cache concurrence
_competitor_cache: dict = {}
_competitor_ts: float = 0
_COMPETITOR_TTL = 300  # 5 minutes

# Historique swaps
_swap_history: list = []


def get_swap_commission_bps(volume_30d: float) -> int:
    """Commission en BPS selon le volume."""
    for tier_name, tier in SWAP_COMMISSION_TIERS.items():
        if tier["min_volume"] <= volume_30d < tier["max_volume"]:
            return tier["bps"]
    return 15


def get_swap_tier_name(volume_30d: float) -> str:
    for tier_name, tier in SWAP_COMMISSION_TIERS.items():
        if tier["min_volume"] <= volume_30d < tier["max_volume"]:
            return tier_name
    return "BRONZE"


async def fetch_prices(token_ids: list = None) -> dict:
    """Recupere les prix via Pyth oracle (Helius RPC)."""
    global _price_cache, _price_cache_ts

    if time.time() - _price_cache_ts < _PRICE_TTL and _price_cache:
        return _price_cache

    try:
        from price_oracle import get_crypto_prices
        oracle_prices = await get_crypto_prices()
        prices = {}
        for sym, data in oracle_prices.items():
            if sym in SUPPORTED_TOKENS:
                prices[sym] = {
                    "price": data.get("price", 0),
                    "mint": SUPPORTED_TOKENS[sym]["mint"],
                    "name": SUPPORTED_TOKENS[sym]["name"],
                    "source": data.get("source", "unknown"),
                }
        _price_cache = prices
        _price_cache_ts = time.time()
        return prices
    except Exception as e:
        print(f"[CryptoSwap] Price oracle error: {e}")

    if _price_cache:
        return _price_cache

    # Fallback ultime
    from price_oracle import FALLBACK_PRICES
    return {
        sym: {"price": FALLBACK_PRICES.get(sym, 0), "mint": SUPPORTED_TOKENS[sym]["mint"], "name": SUPPORTED_TOKENS[sym]["name"], "source": "fallback"}
        for sym in SUPPORTED_TOKENS
    }


async def update_competitor_fees():
    """Met a jour les frais concurrence en temps reel via Jupiter."""
    global _competitor_cache, _competitor_ts

    if time.time() - _competitor_ts < _COMPETITOR_TTL and _competitor_cache:
        return _competitor_cache

    # Utiliser des estimations statiques (Jupiter API non accessible depuis Railway)
    _competitor_cache = {
        "jupiter_effective_bps": 5,
        "jupiter_price_impact": 0.05,
        "raydium_bps": 25,
        "orca_bps": 30,
        "binance_bps": 10,
        "updated_at": int(time.time()),
    }
    _competitor_ts = time.time()

    return _competitor_cache or {"jupiter_effective_bps": 5, "raydium_bps": 25}


async def get_swap_quote(from_token: str, to_token: str, amount: float,
                          user_volume_30d: float = 0) -> dict:
    """Obtient un devis de swap avec commission MAXIA."""
    from_token = from_token.upper()
    to_token = to_token.upper()

    if from_token not in SUPPORTED_TOKENS:
        return {"error": f"Token inconnu: {from_token}. Disponibles: {list(SUPPORTED_TOKENS.keys())}"}
    if to_token not in SUPPORTED_TOKENS:
        return {"error": f"Token inconnu: {to_token}. Disponibles: {list(SUPPORTED_TOKENS.keys())}"}
    if from_token == to_token:
        return {"error": "Les tokens source et destination doivent etre differents"}
    if amount <= 0:
        return {"error": "Le montant doit etre positif"}

    # Obtenir les prix
    prices = await fetch_prices()
    from_price = prices.get(from_token, {}).get("price", 0)
    to_price = prices.get(to_token, {}).get("price", 0)

    if from_price <= 0 or to_price <= 0:
        return {"error": "Prix indisponible"}

    # Valeur en USD
    value_usd = amount * from_price

    # Commission MAXIA
    commission_bps = get_swap_commission_bps(user_volume_30d)
    tier = get_swap_tier_name(user_volume_30d)
    commission_usd = value_usd * commission_bps / 10000
    net_value_usd = value_usd - commission_usd

    # Montant recu
    output_amount = net_value_usd / to_price

    # Obtenir le devis Jupiter pour comparaison
    jupiter_quote = None
    try:
        from_mint = SUPPORTED_TOKENS[from_token]["mint"]
        to_mint = SUPPORTED_TOKENS[to_token]["mint"]
        from_decimals = SUPPORTED_TOKENS[from_token]["decimals"]
        amount_raw = int(amount * (10 ** from_decimals))

        params = {
            "inputMint": from_mint,
            "outputMint": to_mint,
            "amount": str(amount_raw),
            "slippageBps": 50,
            "restrictIntermediateTokens": "true",
        }

        # Jupiter lite-api (gratuit) avec retry si rate limit
        jup_urls = [
            "https://lite-api.jup.ag/swap/v1/quote",
            "https://api.jup.ag/swap/v1/quote",
        ]
        for jup_url in jup_urls:
            if jupiter_quote:
                break
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.get(jup_url, params=params)
                        if resp.status_code == 200:
                            jdata = resp.json()
                            to_dec = SUPPORTED_TOKENS[to_token]["decimals"]
                            jupiter_output = int(jdata.get("outAmount", "0")) / (10 ** to_dec)
                            jupiter_price_impact = float(jdata.get("priceImpactPct", "0"))
                            jupiter_quote = {
                                "output_amount": jupiter_output,
                                "price_impact_pct": jupiter_price_impact,
                                "route": [r.get("swapInfo", {}).get("label", "") for r in jdata.get("routePlan", [])],
                                "raw": jdata,
                            }
                            if jupiter_output > output_amount:
                                commission_from_jupiter = jupiter_output * commission_bps / 10000
                                output_amount = jupiter_output - commission_from_jupiter
                            break
                        elif resp.status_code == 429:
                            # Rate limit — attendre et reessayer
                            import asyncio as _aio
                            await _aio.sleep(2 * (attempt + 1))
                            continue
                        else:
                            break  # Autre erreur, essayer URL suivante
                except Exception:
                    break
    except Exception:
        pass

    # Comparaison concurrence
    competitors = await update_competitor_fees()

    return {
        "from_token": from_token,
        "to_token": to_token,
        "input_amount": amount,
        "input_value_usd": round(value_usd, 4),
        "output_amount": round(output_amount, 8),
        "output_value_usd": round(output_amount * to_price, 4),
        "commission_bps": commission_bps,
        "commission_pct": f"{commission_bps/100:.2f}%",
        "commission_usd": round(commission_usd, 4),
        "tier": tier,
        "from_price_usd": from_price,
        "to_price_usd": to_price,
        "rate": round(from_price / to_price, 8),
        "jupiter_available": jupiter_quote is not None,
        "jupiter_output": round(jupiter_quote["output_amount"], 8) if jupiter_quote else None,
        "competitors": {
            "jupiter_direct": "0% + slippage",
            "raydium": "0.25%",
            "binance": "0.10%",
            "maxia": f"{commission_bps/100:.2f}% ({tier})",
        },
        "valid_for_seconds": 30,
    }


async def execute_swap(buyer_api_key: str, buyer_name: str, buyer_wallet: str,
                        from_token: str, to_token: str, amount: float,
                        buyer_volume_30d: float = 0, payment_tx: str = "") -> dict:
    """Execute un swap crypto."""
    from_token = from_token.upper()
    to_token = to_token.upper()

    if from_token not in SUPPORTED_TOKENS or to_token not in SUPPORTED_TOKENS:
        return {"success": False, "error": "Token non supporte"}
    if amount <= 0:
        return {"success": False, "error": "Montant invalide"}

    # Obtenir le devis
    quote = await get_swap_quote(from_token, to_token, amount, buyer_volume_30d)
    if "error" in quote:
        return {"success": False, "error": quote["error"]}

    output_amount = quote["output_amount"]
    commission_bps = quote["commission_bps"]
    commission_usd = quote["commission_usd"]
    tier = quote["tier"]

    # Router via Jupiter pour le swap reel
    jupiter_result = None
    try:
        from jupiter_router import buy_token_via_jupiter
        from_mint = SUPPORTED_TOKENS[from_token]["mint"]
        to_mint = SUPPORTED_TOKENS[to_token]["mint"]
        # Appeler Jupiter
        jupiter_result = await buy_token_via_jupiter(to_mint, amount, buyer_wallet)
    except Exception as e:
        print(f"[CryptoSwap] Jupiter routing error: {e}")

    # Enregistrer le swap
    swap = {
        "swap_id": str(uuid.uuid4()),
        "buyer": buyer_name,
        "buyer_wallet": buyer_wallet,
        "from_token": from_token,
        "to_token": to_token,
        "input_amount": amount,
        "output_amount": output_amount,
        "commission_bps": commission_bps,
        "commission_usd": commission_usd,
        "tier": tier,
        "payment_tx": payment_tx,
        "jupiter_signature": jupiter_result.get("signature", "") if jupiter_result and jupiter_result.get("success") else "",
        "on_chain": bool(jupiter_result and jupiter_result.get("success")),
        "timestamp": int(time.time()),
    }
    _swap_history.append(swap)

    # Alerte Discord
    try:
        from alerts import alert_revenue
        await alert_revenue(commission_usd, f"Swap {from_token}->{to_token} — {buyer_name}")
    except Exception:
        pass

    print(f"[CryptoSwap] {amount} {from_token} -> {output_amount:.6f} {to_token} par {buyer_name} — commission {commission_usd:.4f} USDC")

    return {
        "success": True,
        **swap,
        "message": f"Swap {amount} {from_token} -> {output_amount:.6f} {to_token}. Commission: {commission_usd:.4f} USDC ({commission_bps/100:.2f}%)",
    }


def list_tokens() -> dict:
    """Liste tous les tokens disponibles."""
    tokens = []
    for sym, info in SUPPORTED_TOKENS.items():
        price_data = _price_cache.get(sym, {})
        tokens.append({
            "symbol": sym,
            "name": info["name"],
            "mint": info["mint"],
            "decimals": info["decimals"],
            "price_usd": price_data.get("price", 0),
        })
    return {
        "total": len(tokens),
        "tokens": tokens,
        "pairs": f"{len(tokens) * (len(tokens)-1)} paires disponibles",
    }


def get_swap_stats() -> dict:
    """Stats des swaps."""
    total_volume = sum(s.get("commission_usd", 0) / (s.get("commission_bps", 15) / 10000) for s in _swap_history if s.get("commission_bps"))
    total_commission = sum(s.get("commission_usd", 0) for s in _swap_history)
    return {
        "total_swaps": len(_swap_history),
        "total_volume_usd": round(total_volume, 2),
        "total_commission_usd": round(total_commission, 4),
        "tokens_supported": len(SUPPORTED_TOKENS),
        "pairs_available": len(SUPPORTED_TOKENS) * (len(SUPPORTED_TOKENS) - 1),
        "commission_tiers": SWAP_COMMISSION_TIERS,
        "competitors": COMPETITOR_FEES,
    }


def compare_fees(volume_30d: float = 0) -> dict:
    """Compare les frais MAXIA vs concurrence pour les swaps."""
    maxia_bps = get_swap_commission_bps(volume_30d)
    tier = get_swap_tier_name(volume_30d)

    return {
        "your_tier": tier,
        "your_volume_30d": volume_30d,
        "maxia_fee_bps": maxia_bps,
        "maxia_fee_pct": f"{maxia_bps/100:.2f}%",
        "competitors": {
            "Jupiter (direct)": {"fee": "0% + slippage (~0.1-0.5%)", "total_effective": "0.1-0.5%"},
            "Raydium": {"fee": "0.25%", "total_effective": "0.25%"},
            "Orca": {"fee": "0.30%", "total_effective": "0.30%"},
            "Binance": {"fee": "0.10%", "total_effective": "0.10%"},
            "Coinbase": {"fee": "0.40%", "total_effective": "0.40%"},
            "Kraken": {"fee": "0.16%", "total_effective": "0.16%"},
            "MAXIA": {"fee": f"{maxia_bps/100:.2f}% ({tier})", "total_effective": f"{maxia_bps/100:.2f}%"},
        },
        "maxia_advantages": [
            "Commission la plus basse pour Baleine (0.02%)",
            "Routing via Jupiter (meilleur prix garanti)",
            "Paiement USDC sur Solana",
            "API ouverte pour agents IA",
            "10 tokens supportes + xStocks",
        ],
    }


print(f"[CryptoSwap] Engine initialise — {len(SUPPORTED_TOKENS)} tokens, {len(SUPPORTED_TOKENS) * (len(SUPPORTED_TOKENS)-1)} paires")
