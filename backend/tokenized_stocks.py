"""MAXIA Art.23 V11 — Bourse d'Actions Tokenisees (xStocks/Ondo via Jupiter)

Agrege les actions tokenisees sur Solana :
- Backed Finance xStocks (TSLAX, AAPLX, NVDAX, GOOGLX...)
- Ondo Global Markets (AAPLon, TSLAon, NVDAon...)
Commission dynamique la plus basse du marche.
"""
import asyncio, time, uuid, json
import httpx
from config import TREASURY_ADDRESS, get_rpc_url

# ── Catalogue des actions tokenisees sur Solana ──
# Mint addresses des principaux xStocks et Ondo tokens
TOKENIZED_STOCKS = {
    "AAPL": {
        "name": "Apple Inc.",
        "symbol": "AAPL",
        "xstock_symbol": "AAPLX",
        "ondo_symbol": "AAPLon",
        "sector": "Technology",
        "mint_xstock": "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/apple.com",
    },
    "TSLA": {
        "name": "Tesla Inc.",
        "symbol": "TSLA",
        "xstock_symbol": "TSLAX",
        "ondo_symbol": "TSLAon",
        "sector": "Automotive",
        "mint_xstock": "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/tesla.com",
    },
    "NVDA": {
        "name": "NVIDIA Corp.",
        "symbol": "NVDA",
        "xstock_symbol": "NVDAX",
        "ondo_symbol": "NVDAon",
        "sector": "Technology",
        "mint_xstock": "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/nvidia.com",
    },
    "GOOGL": {
        "name": "Alphabet Inc.",
        "symbol": "GOOGL",
        "xstock_symbol": "GOOGLX",
        "ondo_symbol": "GOOGLon",
        "sector": "Technology",
        "mint_xstock": "XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/google.com",
    },
    "MSFT": {
        "name": "Microsoft Corp.",
        "symbol": "MSFT",
        "xstock_symbol": "MSFTX",
        "ondo_symbol": "MSFTon",
        "sector": "Technology",
        "mint_xstock": "XsMTBZsqrDgTRWKzKMGSDE8GQjPX4mNQHN3fLFMKfBJ",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/microsoft.com",
    },
    "AMZN": {
        "name": "Amazon.com Inc.",
        "symbol": "AMZN",
        "xstock_symbol": "AMZNX",
        "ondo_symbol": "AMZNon",
        "sector": "Consumer",
        "mint_xstock": "Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/amazon.com",
    },
    "META": {
        "name": "Meta Platforms Inc.",
        "symbol": "META",
        "xstock_symbol": "METAX",
        "ondo_symbol": "METAon",
        "sector": "Technology",
        "mint_xstock": "XsoeC2iBhNSXVgVB9GNofBSVw3VF9LDLBqSMhRdZi43",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/meta.com",
    },
    "MSTR": {
        "name": "MicroStrategy Inc.",
        "symbol": "MSTR",
        "xstock_symbol": "MSTRX",
        "ondo_symbol": "MSTRon",
        "sector": "Technology/Bitcoin",
        "mint_xstock": "XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ",
        "mint_ondo": "",
        "logo": "https://logo.clearbit.com/microstrategy.com",
    },
    "QQQ": {
        "name": "Invesco QQQ Trust (Nasdaq 100 ETF)",
        "symbol": "QQQ",
        "xstock_symbol": "QQQX",
        "ondo_symbol": "QQQon",
        "sector": "ETF",
        "mint_xstock": "Xs8S1uUs1zvS2p7iwtsG3b6fkhpvmwz4GYU3gWAmWHZ",
        "mint_ondo": "",
        "logo": "",
    },
    "SPY": {
        "name": "SPDR S&P 500 ETF",
        "symbol": "SPY",
        "xstock_symbol": "SPYX",
        "ondo_symbol": "SPYon",
        "sector": "ETF",
        "mint_xstock": "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W",
        "mint_ondo": "",
        "logo": "",
    },
}

# ── Commission dynamique pour les actions (plus basse que les services) ──
STOCK_COMMISSION_TIERS = {
    "BRONZE": {"min_volume": 0, "max_volume": 1000, "bps": 50},       # 0.5%
    "ARGENT": {"min_volume": 1000, "max_volume": 5000, "bps": 20},    # 0.2%
    "OR": {"min_volume": 5000, "max_volume": 25000, "bps": 10},       # 0.1%
    "BALEINE": {"min_volume": 25000, "max_volume": float("inf"), "bps": 5},  # 0.05%
}

# Commissions concurrents (pour comparaison et ajustement)
COMPETITOR_FEES = {
    "jupiter": {"name": "Jupiter", "fee_bps": 0, "note": "0% swap mais 0.3-1% slippage pool"},
    "raydium": {"name": "Raydium", "fee_bps": 25, "note": "0.25% pool fee"},
    "robinhood": {"name": "Robinhood", "fee_bps": 50, "note": "0% affiche mais ~0.5% spread cache"},
    "etoro": {"name": "eToro", "fee_bps": 100, "note": "1% par trade"},
    "binance": {"name": "Binance", "fee_bps": 10, "note": "0.1% maker/taker"},
}

# ── Auto-decouverte des tokens xStocks/Ondo sur Solana ──

_discovery_cache: list = []
_discovery_ts: float = 0
_DISCOVERY_TTL = 3600  # 1 heure


async def auto_discover_xstocks() -> list:
    """Scanne Jupiter et Backed pour trouver automatiquement les nouvelles actions tokenisees."""
    global _discovery_cache, _discovery_ts, TOKENIZED_STOCKS

    if time.time() - _discovery_ts < _DISCOVERY_TTL and _discovery_cache:
        return _discovery_cache

    discovered = []
    try:
        # 1. Scanner Jupiter verified tokens avec tag "tokenized-stock" ou "xstock"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://tokens.jup.ag/tokens?tags=verified")
            if resp.status_code == 200:
                tokens = resp.json()
                for token in tokens:
                    name = token.get("name", "").lower()
                    symbol = token.get("symbol", "").upper()
                    # Detecter les xStocks (finissent par X) et Ondo (finissent par on)
                    is_xstock = (symbol.endswith("X") and len(symbol) >= 4 and
                                 any(kw in name.lower() for kw in ["stock", "equity", "backed", "tokenized"]))
                    is_ondo = ("ondo" in name.lower() or symbol.endswith("ON") and
                               any(kw in name.lower() for kw in ["apple", "tesla", "nvidia", "google", "microsoft", "amazon", "meta"]))

                    if is_xstock or is_ondo:
                        # Extraire le symbole boursier
                        base_sym = symbol.rstrip("X").rstrip("on").upper()
                        if base_sym and base_sym not in TOKENIZED_STOCKS:
                            new_stock = {
                                "name": token.get("name", symbol),
                                "symbol": base_sym,
                                "xstock_symbol": symbol,
                                "ondo_symbol": f"{base_sym}on",
                                "sector": "Auto-discovered",
                                "mint_xstock": token.get("address", ""),
                                "mint_ondo": "",
                                "logo": token.get("logoURI", ""),
                            }
                            TOKENIZED_STOCKS[base_sym] = new_stock
                            discovered.append({"symbol": base_sym, "name": token.get("name", ""), "mint": token.get("address", "")})
                            print(f"[Stocks] Auto-discovered: {base_sym} ({token.get('name', '')})")

    except Exception as e:
        print(f"[Stocks] Auto-discovery error: {e}")

    # 2. Scanner les tokens Backed Finance directement
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.backed.fi/v1/tokens", headers={"Accept": "application/json"})
            if resp.status_code == 200:
                backed_tokens = resp.json()
                if isinstance(backed_tokens, list):
                    for bt in backed_tokens:
                        sym = bt.get("symbol", "").rstrip("X").upper()
                        if sym and sym not in TOKENIZED_STOCKS:
                            chain_data = bt.get("chains", {}).get("solana", {})
                            mint = chain_data.get("address", "") if chain_data else ""
                            new_stock = {
                                "name": bt.get("name", sym),
                                "symbol": sym,
                                "xstock_symbol": bt.get("symbol", f"{sym}X"),
                                "ondo_symbol": f"{sym}on",
                                "sector": bt.get("category", "Auto-discovered"),
                                "mint_xstock": mint,
                                "mint_ondo": "",
                                "logo": bt.get("logo", ""),
                            }
                            TOKENIZED_STOCKS[sym] = new_stock
                            discovered.append({"symbol": sym, "name": bt.get("name", ""), "mint": mint})
                            print(f"[Stocks] Backed discovered: {sym}")
    except Exception as e:
        print(f"[Stocks] Backed API error: {e}")

    # 3. Scanner Ondo Global Markets
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.ondo.finance/v1/tokens", headers={"Accept": "application/json"})
            if resp.status_code == 200:
                ondo_tokens = resp.json()
                if isinstance(ondo_tokens, list):
                    for ot in ondo_tokens:
                        sym = ot.get("underlying", "").upper()
                        if sym and sym not in TOKENIZED_STOCKS:
                            new_stock = {
                                "name": ot.get("name", sym),
                                "symbol": sym,
                                "xstock_symbol": f"{sym}X",
                                "ondo_symbol": ot.get("symbol", f"{sym}on"),
                                "sector": ot.get("category", "Auto-discovered"),
                                "mint_xstock": "",
                                "mint_ondo": ot.get("address", ""),
                                "logo": ot.get("logo", ""),
                            }
                            TOKENIZED_STOCKS[sym] = new_stock
                            discovered.append({"symbol": sym, "name": ot.get("name", "")})
                            print(f"[Stocks] Ondo discovered: {sym}")
    except Exception as e:
        print(f"[Stocks] Ondo API error: {e}")

    _discovery_cache = discovered
    _discovery_ts = time.time()

    if discovered:
        print(f"[Stocks] Auto-discovery: {len(discovered)} nouvelles actions trouvees")
        try:
            from alerts import alert_system
            import asyncio
            await alert_system(
                "📈 Nouvelles actions tokenisees",
                f"{len(discovered)} nouvelles actions ajoutees automatiquement:\n"
                + "\n".join([f"  - {d['symbol']}: {d['name']}" for d in discovered[:10]]),
            )
        except Exception:
            pass

    return discovered


# Prix cache (mis a jour periodiquement)
_price_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 60  # 60 secondes

# Historique trades
_stock_trades: list = []
_portfolios: dict = {}  # api_key -> {symbol: amount}


def get_stock_commission_bps(volume_30d: float) -> int:
    """Retourne la commission en BPS selon le volume 30j."""
    for tier_name, tier in STOCK_COMMISSION_TIERS.items():
        if tier["min_volume"] <= volume_30d < tier["max_volume"]:
            return tier["bps"]
    return 50  # defaut Bronze


def get_stock_tier_name(volume_30d: float) -> str:
    for tier_name, tier in STOCK_COMMISSION_TIERS.items():
        if tier["min_volume"] <= volume_30d < tier["max_volume"]:
            return tier_name
    return "BRONZE"


async def fetch_stock_prices() -> dict:
    """Recupere les prix des actions via Pyth oracle (Helius RPC)."""
    global _price_cache, _cache_ts

    if time.time() - _cache_ts < _CACHE_TTL and _price_cache:
        return _price_cache

    prices = {}
    symbols = list(TOKENIZED_STOCKS.keys())

    try:
        from price_oracle import get_stock_prices
        oracle_prices = await get_stock_prices()
        for sym in symbols:
            data = oracle_prices.get(sym, {})
            price = data.get("price", 0)
            source = data.get("source", "fallback")
            prices[sym] = {
                "price": price,
                "change": 0,
                "volume": 0,
                "market_cap": 0,
                "name": TOKENIZED_STOCKS[sym]["name"],
                "source": source,
            }
    except Exception as e:
        print(f"[Stocks] Price oracle error: {e}")

    # Fallback si rien
    if not prices:
        from price_oracle import FALLBACK_PRICES
        for sym in symbols:
            if sym in TOKENIZED_STOCKS:
                prices[sym] = {
                    "price": FALLBACK_PRICES.get(sym, 0), "change": 0, "volume": 0,
                    "market_cap": 0, "name": TOKENIZED_STOCKS[sym]["name"], "source": "fallback",
                }

    _price_cache = prices
    _cache_ts = time.time()
    return prices


class TokenizedStockExchange:
    """Bourse d'actions tokenisees MAXIA."""

    def __init__(self):
        print("[Stocks] Bourse d'actions tokenisees initialisee — "
              f"{len(TOKENIZED_STOCKS)} actions disponibles")

    async def list_stocks(self) -> dict:
        # Auto-decouverte des nouvelles actions
        await auto_discover_xstocks()
        """Liste toutes les actions disponibles avec prix."""
        prices = await fetch_stock_prices()
        stocks = []
        for sym, info in TOKENIZED_STOCKS.items():
            price_data = prices.get(sym, {})
            stocks.append({
                "symbol": sym,
                "name": info["name"],
                "sector": info["sector"],
                "xstock": info["xstock_symbol"],
                "ondo": info["ondo_symbol"],
                "price_usd": price_data.get("price", 0),
                "change_24h_pct": round(price_data.get("change", 0), 2),
                "volume": price_data.get("volume", 0),
                "fractional": True,
                "min_buy_usdc": 1.0,
                "payment": "USDC on Solana",
            })

        return {
            "total": len(stocks),
            "stocks": stocks,
            "providers": ["Backed Finance (xStocks)", "Ondo Global Markets"],
            "commission": {
                "bronze": "0.5% (0-1K USDC/mois)",
                "argent": "0.2% (1K-5K USDC/mois)",
                "or": "0.1% (5K-25K USDC/mois)",
                "baleine": "0.05% (25K+ USDC/mois)",
            },
            "note": "Commission la plus basse du marche. Achat fractionne a partir de 1 USDC.",
        }

    async def get_price(self, symbol: str) -> dict:
        """Prix temps reel d'une action."""
        symbol = symbol.upper()
        if symbol not in TOKENIZED_STOCKS:
            return {"error": f"Action inconnue: {symbol}. Disponibles: {list(TOKENIZED_STOCKS.keys())}"}

        prices = await fetch_stock_prices()
        price_data = prices.get(symbol, {})
        info = TOKENIZED_STOCKS[symbol]

        return {
            "symbol": symbol,
            "name": info["name"],
            "price_usd": price_data.get("price", 0),
            "change_24h_pct": round(price_data.get("change", 0), 2),
            "volume": price_data.get("volume", 0),
            "market_cap": price_data.get("market_cap", 0),
            "tokens_available": [info["xstock_symbol"], info["ondo_symbol"]],
            "sector": info["sector"],
            "updated_at": int(time.time()),
        }

    async def buy_stock(self, buyer_api_key: str, buyer_name: str,
                         buyer_wallet: str, symbol: str, amount_usdc: float,
                         buyer_volume_30d: float, payment_tx: str = "") -> dict:
        """Acheter des actions tokenisees."""
        symbol = symbol.upper()
        if symbol not in TOKENIZED_STOCKS:
            return {"success": False, "error": f"Action inconnue: {symbol}"}
        if amount_usdc < 1.0:
            return {"success": False, "error": "Minimum 1 USDC"}
        if amount_usdc > 100000:
            return {"success": False, "error": "Maximum 100 000 USDC par trade"}

        prices = await fetch_stock_prices()
        price = prices.get(symbol, {}).get("price", 0)
        if price <= 0:
            return {"success": False, "error": f"Prix indisponible pour {symbol}"}

        # Calculer la commission
        commission_bps = get_stock_commission_bps(buyer_volume_30d)
        commission = round(amount_usdc * commission_bps / 10000, 4)
        net_amount = amount_usdc - commission
        shares = round(net_amount / price, 6)
        tier = get_stock_tier_name(buyer_volume_30d)

        # Router via Jupiter pour le swap reel USDC -> Token
        jupiter_result = None
        mint = TOKENIZED_STOCKS[symbol].get("mint_xstock") or TOKENIZED_STOCKS[symbol].get("mint_ondo", "")
        if mint and len(mint) > 20:
            try:
                from jupiter_router import buy_token_via_jupiter
                jupiter_result = await buy_token_via_jupiter(mint, net_amount, buyer_wallet)
                if jupiter_result.get("success"):
                    print(f"[Stocks] Jupiter swap OK: {jupiter_result.get('signature', '')[:16]}...")
                else:
                    print(f"[Stocks] Jupiter swap failed: {jupiter_result.get('error', '')} — trade enregistre localement")
            except Exception as e:
                print(f"[Stocks] Jupiter routing error: {e} — trade enregistre localement")

        # Enregistrer le trade
        trade = {
            "trade_id": str(uuid.uuid4()),
            "type": "buy",
            "buyer": buyer_name,
            "buyer_wallet": buyer_wallet,
            "symbol": symbol,
            "name": TOKENIZED_STOCKS[symbol]["name"],
            "amount_usdc": amount_usdc,
            "commission_usdc": commission,
            "commission_bps": commission_bps,
            "net_amount_usdc": net_amount,
            "price_per_share": price,
            "shares": shares,
            "tier": tier,
            "payment_tx": payment_tx,
            "timestamp": int(time.time()),
            "route": "Jupiter -> xStocks/Ondo",
            "jupiter_signature": jupiter_result.get("signature", "") if jupiter_result and jupiter_result.get("success") else "",
            "jupiter_explorer": jupiter_result.get("explorer", "") if jupiter_result and jupiter_result.get("success") else "",
            "on_chain": bool(jupiter_result and jupiter_result.get("success")),
        }
        _stock_trades.append(trade)

        # Mettre a jour le portfolio
        _portfolios.setdefault(buyer_api_key, {})
        _portfolios[buyer_api_key].setdefault(symbol, 0)
        _portfolios[buyer_api_key][symbol] += shares

        # Alerte Discord
        try:
            from alerts import alert_revenue
            await alert_revenue(commission, f"Achat action {symbol} — {buyer_name} ({amount_usdc} USDC)")
        except Exception:
            pass

        print(f"[Stocks] BUY {shares:.4f} {symbol} @ ${price} par {buyer_name} — commission {commission} USDC")

        return {
            "success": True,
            **trade,
            "message": f"Achat de {shares:.4f} actions {symbol} a ${price:.2f}/action. Commission: {commission:.4f} USDC ({commission_bps/100:.2f}%).",
        }

    async def sell_stock(self, seller_api_key: str, seller_name: str,
                          seller_wallet: str, symbol: str, shares: float,
                          seller_volume_30d: float) -> dict:
        """Vendre des actions tokenisees."""
        symbol = symbol.upper()
        if symbol not in TOKENIZED_STOCKS:
            return {"success": False, "error": f"Action inconnue: {symbol}"}

        # Verifier le portfolio
        portfolio = _portfolios.get(seller_api_key, {})
        held = portfolio.get(symbol, 0)
        if held < shares:
            return {"success": False, "error": f"Solde insuffisant: {held:.6f} {symbol} (demande: {shares})"}

        prices = await fetch_stock_prices()
        price = prices.get(symbol, {}).get("price", 0)
        if price <= 0:
            return {"success": False, "error": f"Prix indisponible pour {symbol}"}

        gross_usdc = round(shares * price, 4)
        commission_bps = get_stock_commission_bps(seller_volume_30d)
        commission = round(gross_usdc * commission_bps / 10000, 4)
        net_usdc = gross_usdc - commission
        tier = get_stock_tier_name(seller_volume_30d)

        # Router via Jupiter pour le swap reel Token -> USDC
        jupiter_result = None
        mint = TOKENIZED_STOCKS[symbol].get("mint_xstock") or TOKENIZED_STOCKS[symbol].get("mint_ondo", "")
        if mint and len(mint) > 20:
            try:
                from jupiter_router import sell_token_via_jupiter
                amount_raw = int(shares * 1e6)  # approximation
                jupiter_result = await sell_token_via_jupiter(mint, amount_raw, seller_wallet)
            except Exception as e:
                print(f"[Stocks] Jupiter sell error: {e}")

        # Enregistrer le trade
        trade = {
            "trade_id": str(uuid.uuid4()),
            "type": "sell",
            "seller": seller_name,
            "seller_wallet": seller_wallet,
            "symbol": symbol,
            "name": TOKENIZED_STOCKS[symbol]["name"],
            "shares": shares,
            "price_per_share": price,
            "gross_usdc": gross_usdc,
            "commission_usdc": commission,
            "commission_bps": commission_bps,
            "net_usdc": net_usdc,
            "tier": tier,
            "timestamp": int(time.time()),
            "route": "xStocks/Ondo -> Jupiter -> USDC",
            "jupiter_signature": jupiter_result.get("signature", "") if jupiter_result and jupiter_result.get("success") else "",
            "on_chain": bool(jupiter_result and jupiter_result.get("success")),
        }
        _stock_trades.append(trade)

        # Mettre a jour le portfolio
        _portfolios[seller_api_key][symbol] -= shares

        try:
            from alerts import alert_revenue
            await alert_revenue(commission, f"Vente action {symbol} — {seller_name} ({gross_usdc} USDC)")
        except Exception:
            pass

        print(f"[Stocks] SELL {shares:.4f} {symbol} @ ${price} par {seller_name} — commission {commission} USDC")

        return {
            "success": True,
            **trade,
            "message": f"Vente de {shares:.4f} actions {symbol}. Vous recevez {net_usdc:.4f} USDC. Commission: {commission:.4f} USDC ({commission_bps/100:.2f}%).",
        }

    def get_portfolio(self, api_key: str) -> dict:
        """Portfolio de l'utilisateur."""
        portfolio = _portfolios.get(api_key, {})
        holdings = []
        total_value = 0
        for sym, qty in portfolio.items():
            if qty > 0:
                price = _price_cache.get(sym, {}).get("price", 0)
                value = qty * price
                total_value += value
                holdings.append({
                    "symbol": sym,
                    "name": TOKENIZED_STOCKS.get(sym, {}).get("name", sym),
                    "shares": round(qty, 6),
                    "price_usd": price,
                    "value_usd": round(value, 2),
                })
        return {
            "holdings": holdings,
            "total_value_usd": round(total_value, 2),
            "total_positions": len(holdings),
        }

    def compare_fees(self) -> dict:
        """Compare les frais MAXIA vs concurrence."""
        return {
            "maxia_tiers": {
                name: {
                    "volume_range": f"{t['min_volume']}-{t['max_volume'] if t['max_volume'] != float('inf') else '∞'} USDC",
                    "fee_pct": f"{t['bps']/100:.2f}%",
                    "fee_bps": t["bps"],
                }
                for name, t in STOCK_COMMISSION_TIERS.items()
            },
            "competitors": {
                k: {"fee_pct": f"{v['fee_bps']/100:.2f}%", "note": v["note"]}
                for k, v in COMPETITOR_FEES.items()
            },
            "maxia_advantages": [
                "Commission la plus basse pour les gros volumes (0.05% Baleine)",
                "Transparente — pas de spread cache comme Robinhood",
                "Paiement USDC sur Solana (pas de carte bancaire)",
                "Achat fractionne a partir de 1 USDC",
                "API ouverte pour les agents IA",
                "24/7 trading (pas de fermeture de marche)",
            ],
        }

    def get_stats(self) -> dict:
        """Statistiques de la bourse."""
        buys = [t for t in _stock_trades if t["type"] == "buy"]
        sells = [t for t in _stock_trades if t["type"] == "sell"]
        total_volume = sum(t.get("amount_usdc", 0) for t in buys) + sum(t.get("gross_usdc", 0) for t in sells)
        total_commission = sum(t.get("commission_usdc", 0) for t in _stock_trades)

        return {
            "total_trades": len(_stock_trades),
            "total_buys": len(buys),
            "total_sells": len(sells),
            "total_volume_usdc": round(total_volume, 2),
            "total_commission_usdc": round(total_commission, 4),
            "stocks_available": len(TOKENIZED_STOCKS),
            "providers": ["Backed Finance", "Ondo Global Markets"],
            "commission_tiers": STOCK_COMMISSION_TIERS,
            "unique_holders": len([k for k, v in _portfolios.items() if any(q > 0 for q in v.values())]),
        }


stock_exchange = TokenizedStockExchange()
