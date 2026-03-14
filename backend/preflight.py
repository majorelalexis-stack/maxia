"""MAXIA Preflight V10.1 — Diagnostic systeme avant lancement"""
import os, asyncio
import httpx
from config import (
    ESCROW_ADDRESS, ESCROW_PRIVKEY_B58, get_rpc_url,
    GROQ_API_KEY, GROQ_MODEL, MARKETING_WALLET_ADDRESS,
    DISCORD_WEBHOOK_URL, TREASURY_ADDRESS,
)


async def check_system_ready() -> dict:
    """
    Verification complete avant lancement.
    Retourne un dict avec le statut de chaque composant.
    """
    results = {}

    # 1. Cles obligatoires
    results["escrow_address"] = {
        "ok": bool(ESCROW_ADDRESS),
        "detail": ESCROW_ADDRESS[:12] + "..." if ESCROW_ADDRESS else "MANQUANT",
    }
    results["escrow_privkey"] = {
        "ok": bool(ESCROW_PRIVKEY_B58),
        "detail": "configure" if ESCROW_PRIVKEY_B58 else "MANQUANT",
    }
    results["treasury"] = {
        "ok": bool(TREASURY_ADDRESS),
        "detail": TREASURY_ADDRESS[:12] + "..." if TREASURY_ADDRESS else "MANQUANT",
    }

    # 2. Connexion RPC Solana
    rpc_ok = False
    rpc_detail = ""
    try:
        rpc = get_rpc_url()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getHealth",
            })
            data = resp.json()
        rpc_ok = data.get("result") == "ok"
        rpc_detail = f"{rpc[:40]}... -> {data.get('result', 'erreur')}"
    except Exception as e:
        rpc_detail = f"Connexion echouee: {e}"
    results["solana_rpc"] = {"ok": rpc_ok, "detail": rpc_detail}

    # 3. Groq API
    groq_ok = False
    groq_detail = ""
    if GROQ_API_KEY:
        try:
            from groq import Groq
            g = Groq(api_key=GROQ_API_KEY)
            resp = g.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            groq_ok = bool(resp.choices)
            groq_detail = f"{GROQ_MODEL} -> OK"
        except Exception as e:
            groq_detail = f"Erreur: {e}"
    else:
        groq_detail = "GROQ_API_KEY manquant"
    results["groq_api"] = {"ok": groq_ok, "detail": groq_detail}

    # 4. Marketing wallet
    mkt_ok = bool(MARKETING_WALLET_ADDRESS)
    results["marketing_wallet"] = {
        "ok": mkt_ok,
        "detail": MARKETING_WALLET_ADDRESS[:12] + "..." if mkt_ok else "Non configure (mode simulation)",
    }

    # 5. Discord webhook
    discord_ok = bool(DISCORD_WEBHOOK_URL)
    results["discord_webhook"] = {
        "ok": discord_ok,
        "detail": "Configure" if discord_ok else "Non configure (alertes desactivees)",
    }

    # 6. Base de donnees
    db_ok = False
    try:
        from database import db
        if db._db:
            db_ok = True
        results["database"] = {"ok": db_ok, "detail": "Connectee" if db_ok else "Non connectee (normal avant demarrage)"}
    except Exception:
        results["database"] = {"ok": False, "detail": "Module non charge"}

    # 7. Resume
    total = len(results)
    passed = sum(1 for r in results.values() if r["ok"])
    critical_ok = results["escrow_address"]["ok"] and results["treasury"]["ok"]

    results["_summary"] = {
        "total": total,
        "passed": passed,
        "critical_ok": critical_ok,
        "ready": critical_ok and results["solana_rpc"]["ok"],
    }

    return results


def print_preflight(results: dict):
    """Affiche le rapport de preflight en console."""
    print("\n" + "=" * 50)
    print("  MAXIA V10.1 — PRE-FLIGHT CHECK")
    print("=" * 50)

    for key, val in results.items():
        if key.startswith("_"):
            continue
        status = "✓" if val["ok"] else "✗"
        print(f"  {status} {key}: {val['detail']}")

    summary = results.get("_summary", {})
    print(f"\n  {summary.get('passed', 0)}/{summary.get('total', 0)} checks OK")
    if summary.get("ready"):
        print("  ✅ Systeme pret pour le lancement")
    else:
        print("  ⚠️  Certains composants manquent")
    print("=" * 50 + "\n")
