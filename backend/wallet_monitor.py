"""MAXIA Art.27 — Wallet Monitor Service

Surveille des wallets Solana en temps reel et alerte quand :
- Un transfert SOL/USDC est detecte
- Le solde change significativement
- Un swap est effectue
- Un token est recu ou envoye

Les IA paient un abonnement mensuel ou par alerte.
"""
import asyncio, time, uuid
import httpx
from http_client import get_http_client
from config import get_rpc_url

# Wallets surveilles: {monitor_id: {wallet, owner_api_key, webhook_url, ...}}
_monitors: dict = {}

# Historique des alertes
_alert_history: list = []

# Stats
_monitor_stats = {"total_monitors": 0, "total_alerts": 0, "active": 0}

_running = False

print("[WalletMonitor] Service initialise")


async def add_monitor(api_key: str, owner_name: str, wallet_address: str,
                       webhook_url: str = "", alert_types: list = None,
                       min_sol_change: float = 0.1) -> dict:
    """Ajoute un wallet a surveiller."""
    if not wallet_address or len(wallet_address) < 32:
        return {"success": False, "error": "Adresse wallet invalide"}

    # Verifier que le wallet existe
    balance = await _get_balance(wallet_address)
    if balance < 0:
        return {"success": False, "error": "Wallet introuvable sur Solana"}

    monitor_id = f"mon_{uuid.uuid4().hex[:12]}"

    if not alert_types:
        alert_types = ["transfer", "balance_change", "token_received"]

    _monitors[monitor_id] = {
        "monitor_id": monitor_id,
        "wallet": wallet_address,
        "owner_api_key": api_key,
        "owner_name": owner_name,
        "webhook_url": webhook_url,
        "alert_types": alert_types,
        "min_sol_change": min_sol_change,
        "last_balance": balance,
        "last_signature": "",
        "last_check": int(time.time()),
        "created_at": int(time.time()),
        "alerts_sent": 0,
        "active": True,
    }

    _monitor_stats["total_monitors"] += 1
    _monitor_stats["active"] = sum(1 for m in _monitors.values() if m["active"])

    print(f"[WalletMonitor] Nouveau moniteur: {wallet_address[:8]}... par {owner_name}")

    return {
        "success": True,
        "monitor_id": monitor_id,
        "wallet": wallet_address,
        "current_balance_sol": balance,
        "alert_types": alert_types,
        "webhook_url": webhook_url or "Pas de webhook — utilisez GET /wallet-monitor/alerts",
        "message": f"Wallet {wallet_address[:8]}... surveille. Alertes actives.",
    }


async def remove_monitor(api_key: str, monitor_id: str) -> dict:
    """Supprime un moniteur."""
    monitor = _monitors.get(monitor_id)
    if not monitor:
        return {"success": False, "error": "Moniteur introuvable"}
    if monitor["owner_api_key"] != api_key:
        return {"success": False, "error": "Ce moniteur ne vous appartient pas"}

    monitor["active"] = False
    _monitor_stats["active"] = sum(1 for m in _monitors.values() if m["active"])

    return {"success": True, "monitor_id": monitor_id, "status": "stopped"}


def get_my_monitors(api_key: str) -> dict:
    """Liste les moniteurs d'un utilisateur."""
    my = [m for m in _monitors.values() if m["owner_api_key"] == api_key]
    return {
        "total": len(my),
        "monitors": [
            {
                "monitor_id": m["monitor_id"],
                "wallet": m["wallet"],
                "current_balance": m["last_balance"],
                "alerts_sent": m["alerts_sent"],
                "active": m["active"],
                "created_at": m["created_at"],
            }
            for m in my
        ],
    }


def get_alerts(api_key: str, limit: int = 50) -> dict:
    """Recupere les alertes pour un utilisateur."""
    my_alerts = [a for a in _alert_history if a["owner_api_key"] == api_key]
    my_alerts.sort(key=lambda x: x["timestamp"], reverse=True)
    return {
        "total": len(my_alerts),
        "alerts": my_alerts[:limit],
    }


async def _get_balance(wallet: str) -> float:
    """Recupere le solde SOL."""
    rpc = get_rpc_url()
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet]}
        client = get_http_client()
        resp = await client.post(rpc, json=payload, timeout=10)
        data = resp.json()
        return data.get("result", {}).get("value", 0) / 1e9
    except Exception:
        return -1


async def _get_recent_signatures(wallet: str, limit: int = 5) -> list:
    """Recupere les signatures recentes."""
    rpc = get_rpc_url()
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": limit}],
        }
        client = get_http_client()
        resp = await client.post(rpc, json=payload, timeout=10)
        data = resp.json()
        return data.get("result", [])
    except Exception:
        return []


async def _send_alert(monitor: dict, alert_type: str, details: dict):
    """Envoie une alerte (webhook ou stockage)."""
    alert = {
        "alert_id": f"alert_{uuid.uuid4().hex[:12]}",
        "monitor_id": monitor["monitor_id"],
        "wallet": monitor["wallet"],
        "owner_api_key": monitor["owner_api_key"],
        "owner_name": monitor["owner_name"],
        "alert_type": alert_type,
        "details": details,
        "timestamp": int(time.time()),
    }

    _alert_history.append(alert)
    monitor["alerts_sent"] += 1
    _monitor_stats["total_alerts"] += 1

    # Limiter l'historique a 1000 alertes
    if len(_alert_history) > 1000:
        _alert_history[:] = _alert_history[-500:]

    # Envoyer le webhook si configure
    if monitor.get("webhook_url"):
        try:
            client = get_http_client()
            await client.post(monitor["webhook_url"], json=alert, timeout=10)
        except Exception as e:
            print(f"[WalletMonitor] Webhook error: {e}")

    print(f"[WalletMonitor] Alert [{alert_type}]: {monitor['wallet'][:8]}... — {details.get('message', '')[:60]}")


async def _check_wallet(monitor: dict):
    """Verifie un wallet pour des changements."""
    wallet = monitor["wallet"]

    # 1. Verifier le solde
    new_balance = await _get_balance(wallet)
    if new_balance < 0:
        return

    old_balance = monitor["last_balance"]
    balance_change = new_balance - old_balance

    if abs(balance_change) >= monitor["min_sol_change"]:
        direction = "recu" if balance_change > 0 else "envoye"
        await _send_alert(monitor, "balance_change", {
            "message": f"{abs(balance_change):.4f} SOL {direction}",
            "old_balance": round(old_balance, 4),
            "new_balance": round(new_balance, 4),
            "change": round(balance_change, 4),
            "direction": "in" if balance_change > 0 else "out",
        })

    monitor["last_balance"] = new_balance

    # 2. Verifier les nouvelles transactions
    sigs = await _get_recent_signatures(wallet, limit=3)
    if sigs and monitor["last_signature"]:
        for sig_info in sigs:
            sig = sig_info.get("signature", "")
            if sig == monitor["last_signature"]:
                break
            # Nouvelle transaction detectee
            await _send_alert(monitor, "transaction", {
                "message": f"Nouvelle transaction detectee",
                "signature": sig,
                "explorer": f"https://solscan.io/tx/{sig}",
                "block_time": sig_info.get("blockTime", 0),
            })

    if sigs:
        monitor["last_signature"] = sigs[0].get("signature", "")

    monitor["last_check"] = int(time.time())


async def run_monitor_loop():
    """Boucle principale de surveillance."""
    global _running
    _running = True

    print("[WalletMonitor] Boucle de surveillance demarree")

    while _running:
        try:
            active_monitors = [m for m in _monitors.values() if m["active"]]

            for monitor in active_monitors:
                await _check_wallet(monitor)
                await asyncio.sleep(2)  # Rate limit RPC

        except Exception as e:
            print(f"[WalletMonitor] Loop error: {e}")

        # Verifier toutes les 30 secondes
        await asyncio.sleep(30)


def stop():
    global _running
    _running = False


def get_monitor_stats() -> dict:
    return {
        **_monitor_stats,
        "monitors": len(_monitors),
        "alert_history_size": len(_alert_history),
    }
