"""Fleet Manager — gestion de flotte d'agents pour les entreprises.

Permet a une entreprise de gerer tous ses agents depuis un seul endpoint.
"""
import logging
import time


async def get_fleet_overview(owner_wallet: str, db) -> dict:
    """Vue d'ensemble de tous les agents d'un wallet proprietaire."""
    try:
        agents = await db.raw_execute_fetchall(
            "SELECT * FROM agents WHERE wallet=? OR referred_by=?",
            (owner_wallet, owner_wallet))

        fleet = []
        total_revenue = 0
        total_swaps = 0

        for agent in agents:
            a = dict(agent)
            api_key = a.get("api_key", "")
            # Get stats per agent
            volume = 0
            try:
                volume = await db.get_swap_volume_30d(a.get("wallet", ""))
            except Exception:
                pass
            swap_count = 0
            try:
                swap_count = await db.get_swap_count(a.get("wallet", ""))
            except Exception:
                pass

            fleet.append({
                "api_key": api_key[:8] + "..." if api_key else "N/A",
                "name": a.get("name", ""),
                "wallet": a.get("wallet", ""),
                "tier": a.get("tier", "BRONZE"),
                "volume_30d": round(volume, 2),
                "swap_count": swap_count,
                "status": "active",
                "created_at": a.get("created_at", 0),
            })
            total_revenue += volume * 0.001  # Approximate commission
            total_swaps += swap_count

        return {
            "owner": owner_wallet,
            "agent_count": len(fleet),
            "total_volume_30d": round(sum(a["volume_30d"] for a in fleet), 2),
            "total_swaps": total_swaps,
            "estimated_revenue": round(total_revenue, 2),
            "agents": fleet,
        }
    except Exception as e:
        return {"error": "An error occurred", "owner": owner_wallet, "agents": []}


async def toggle_agent(api_key: str, enabled: bool, db) -> dict:
    """Activer/desactiver un agent."""
    try:
        status = "active" if enabled else "paused"
        await db.raw_execute(
            "UPDATE agent_services SET status=? WHERE agent_api_key=?",
            (status, api_key))
        return {"success": True, "api_key": api_key[:8] + "..." if api_key else "N/A", "status": status}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}
