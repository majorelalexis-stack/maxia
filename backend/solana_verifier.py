"""MAXIA Solana Verifier V9"""
import os, httpx, asyncio

SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")

async def verify_transaction(tx_signature: str, expected_wallet: str = None) -> bool:
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [tx_signature, {"encoding": "jsonParsed", "commitment": "confirmed"}]
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(SOLANA_RPC, json=payload)
                data = resp.json()
            result = data.get("result")
            if not result:
                await asyncio.sleep(2 ** attempt)
                continue
            if result.get("meta", {}).get("err") is not None:
                return False
            return True
        except Exception as e:
            print(f"[Verifier] Tentative {attempt+1} echouee: {e}")
            await asyncio.sleep(2 ** attempt)
    return False
