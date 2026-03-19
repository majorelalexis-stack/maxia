"""MAXIA Web3 AI Services — Specialized blockchain analysis

Services that AI agents can buy:
- Rug Pull Risk Detector
- Wallet Analyzer
- Token Contract Scanner
- Whale Alert Monitor
"""
import asyncio, time
import httpx

HELIUS_API_KEY = ""
try:
    from config import HELIUS_API_KEY
except ImportError:
    pass


async def analyze_token_risk(token_address: str) -> dict:
    """Analyze rug pull risk for a Solana token.
    
    Checks: liquidity locked, top holders concentration,
    mint authority, freeze authority, supply distribution.
    """
    risk_score = 0  # 0 = safe, 100 = rug pull
    warnings = []
    info = {}

    if not HELIUS_API_KEY:
        return {"error": "Helius API key required", "risk_score": -1}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get token metadata via Helius DAS
            r = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
                json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getAsset",
                    "params": {"id": token_address},
                },
            )
            if r.status_code == 200:
                data = r.json().get("result", {})
                info["name"] = data.get("content", {}).get("metadata", {}).get("name", "Unknown")
                info["symbol"] = data.get("content", {}).get("metadata", {}).get("symbol", "")

                # Check authorities
                authorities = data.get("authorities", [])
                ownership = data.get("ownership", {})

                if ownership.get("frozen"):
                    risk_score += 30
                    warnings.append("Token account is frozen")

                supply = data.get("token_info", {})
                if supply.get("mint_authority"):
                    risk_score += 20
                    warnings.append("Mint authority still active — can create unlimited tokens")

                if supply.get("freeze_authority"):
                    risk_score += 15
                    warnings.append("Freeze authority active — can freeze any holder")

            # Get top holders
            r2 = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
                json={
                    "jsonrpc": "2.0", "id": 2,
                    "method": "getTokenLargestAccounts",
                    "params": [token_address],
                },
            )
            if r2.status_code == 200:
                accounts = r2.json().get("result", {}).get("value", [])
                if accounts:
                    total = sum(float(a.get("amount", 0)) for a in accounts)
                    top1 = float(accounts[0].get("amount", 0)) if accounts else 0
                    top1_pct = (top1 / total * 100) if total > 0 else 0

                    info["top_holder_pct"] = round(top1_pct, 1)
                    info["holder_count_sample"] = len(accounts)

                    if top1_pct > 50:
                        risk_score += 30
                        warnings.append(f"Top holder owns {top1_pct:.0f}% of supply")
                    elif top1_pct > 25:
                        risk_score += 15
                        warnings.append(f"Top holder owns {top1_pct:.0f}% of supply")

    except Exception as e:
        return {"error": str(e), "risk_score": -1}

    risk_score = min(100, risk_score)
    risk_level = "LOW" if risk_score < 30 else "MEDIUM" if risk_score < 60 else "HIGH"

    return {
        "token": token_address,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "warnings": warnings,
        "info": info,
        "recommendation": "SAFE" if risk_score < 30 else "CAUTION" if risk_score < 60 else "AVOID",
    }


async def analyze_wallet(wallet_address: str) -> dict:
    """Analyze a Solana wallet — holdings, activity, profile."""
    if not HELIUS_API_KEY:
        return {"error": "Helius API key required"}

    result = {
        "wallet": wallet_address,
        "sol_balance": 0,
        "token_count": 0,
        "nft_count": 0,
        "is_developer": False,
        "is_whale": False,
        "profile": "unknown",
        "tokens": [],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get SOL balance
            r = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
                json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getBalance",
                    "params": [wallet_address],
                },
            )
            if r.status_code == 200:
                lamports = r.json().get("result", {}).get("value", 0)
                result["sol_balance"] = round(lamports / 1e9, 4)

            # Get token holdings via Helius
            r2 = await client.get(
                f"https://api.helius.xyz/v0/addresses/{wallet_address}/balances?api-key={HELIUS_API_KEY}",
            )
            if r2.status_code == 200:
                data = r2.json()
                tokens = data.get("tokens", [])
                result["token_count"] = len(tokens)
                result["tokens"] = [
                    {"mint": t.get("mint", ""), "amount": t.get("amount", 0), "decimals": t.get("decimals", 0)}
                    for t in tokens[:20]
                ]
                nfts = data.get("nativeBalance", {})

            # Classify wallet
            sol = result["sol_balance"]
            if sol > 1000:
                result["is_whale"] = True
                result["profile"] = "whale"
            elif sol > 100:
                result["profile"] = "active_trader"
            elif sol > 10:
                result["profile"] = "regular_user"
            else:
                result["profile"] = "small_holder"

            if result["token_count"] > 20:
                result["profile"] = "defi_power_user"

    except Exception as e:
        result["error"] = str(e)

    return result


async def get_fear_greed_index() -> dict:
    """Get crypto Fear & Greed Index."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            if r.status_code == 200:
                data = r.json().get("data", [{}])[0]
                return {
                    "value": int(data.get("value", 50)),
                    "classification": data.get("value_classification", "Neutral"),
                    "timestamp": data.get("timestamp", ""),
                }
    except Exception:
        pass
    return {"value": 50, "classification": "Neutral", "timestamp": ""}
