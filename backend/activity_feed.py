"""MAXIA Activity Feed V12 — Flux d'activite temps reel du marketplace

Enregistre et diffuse les evenements du marketplace (swaps, inscriptions, achats,
GPU, stocks, disputes...) via REST + SSE. Aucune authentification requise (feed public).
Rotation automatique a 1000 evenements. Wallets anonymisees (4 premiers + 4 derniers chars).
"""
import asyncio, json, time
from datetime import datetime, timezone
from fastapi import APIRouter, Query, Request
from starlette.responses import StreamingResponse
from error_utils import safe_error

router = APIRouter(prefix="/api/feed", tags=["activity-feed"])

# ── Types d'evenements ──

EVENT_SWAP = "swap"
EVENT_SERVICE_LISTED = "service_listed"
EVENT_SERVICE_BOUGHT = "service_bought"
EVENT_AGENT_REGISTERED = "agent_registered"
EVENT_STOCK_TRADE = "stock_trade"
EVENT_GPU_RENTED = "gpu_rented"
EVENT_DISPUTE = "dispute_resolved"
EVENT_AUCTION_BID = "auction_bid"
EVENT_LEADERBOARD = "leaderboard_change"
EVENT_EVM_SWAP = "evm_swap"

# ── Schema auto-create ──

_schema_ready = False

_FEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    amount_usdc REAL NOT NULL DEFAULT 0,
    chain TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_feed_created ON activity_feed(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_type ON activity_feed(event_type);
"""


async def _ensure_schema():
    """Cree la table activity_feed si elle n'existe pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from database import db
        await db.raw_executescript(_FEED_SCHEMA)
        _schema_ready = True
        print("[ActivityFeed] Schema pret")
    except Exception as e:
        print(f"[ActivityFeed] Erreur schema: {e}")


# ── SSE : files d'attente pour les clients connectes ──

_sse_queues: list[asyncio.Queue] = []
_MAX_SSE_CLIENTS = 50


# ── Helpers ──

def _anonymize_wallet(wallet: str) -> str:
    """Anonymise un wallet : affiche les 4 premiers + 4 derniers caracteres.
    Ex: '7xKeAb3f...9f2B' -> '7xKe...9f2B'
    """
    if not wallet or len(wallet) <= 8:
        return wallet
    return f"{wallet[:4]}...{wallet[-4:]}"


def _relative_time(iso_str: str) -> str:
    """Convertit un timestamp ISO en temps relatif lisible.
    Retourne 'just now', '2 min ago', '1h ago', '3d ago', etc.
    """
    try:
        # Gere les timestamps avec ou sans Z
        clean = iso_str.replace("Z", "+00:00") if "Z" in iso_str else iso_str
        if "+" not in clean and "T" in clean:
            clean += "+00:00"
        dt = datetime.fromisoformat(clean)
        now = datetime.now(timezone.utc)
        # S'assurer que dt est aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = (now - dt).total_seconds()
    except Exception:
        return "just now"

    if diff < 60:
        return "just now"
    elif diff < 3600:
        mins = int(diff / 60)
        return f"{mins} min ago"
    elif diff < 86400:
        hours = int(diff / 3600)
        return f"{hours}h ago"
    elif diff < 604800:
        days = int(diff / 86400)
        return f"{days}d ago"
    else:
        weeks = int(diff / 604800)
        return f"{weeks}w ago"


# ── Fonction publique : enregistrer un evenement ──

async def record_activity(
    event_type: str,
    actor: str,
    summary: str,
    amount_usdc: float = 0,
    chain: str = "",
    detail: str = "",
):
    """Enregistre un evenement dans le feed d'activite.

    Callable depuis n'importe quel module :
        from activity_feed import record_activity
        await record_activity(EVENT_SWAP, wallet, "Swapped SOL -> USDC", amount_usdc=150, chain="solana")

    - Anonymise automatiquement les wallets dans 'actor'
    - Rotation auto : supprime les plus anciens au-dela de 1000 evenements
    - Pousse l'evenement aux clients SSE connectes
    """
    await _ensure_schema()

    anonymized_actor = _anonymize_wallet(actor)

    try:
        from database import db

        # Inserer le nouvel evenement
        await db.raw_execute(
            "INSERT INTO activity_feed (event_type, actor, summary, amount_usdc, chain, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_type, anonymized_actor, summary, amount_usdc, chain, detail),
        )

        # Rotation : garder max 1000 evenements
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) AS cnt FROM activity_feed"
        )
        count = rows[0]["cnt"] if rows else 0
        if count > 1000:
            overflow = count - 1000
            await db.raw_execute(
                "DELETE FROM activity_feed WHERE id IN "
                "(SELECT id FROM activity_feed ORDER BY id ASC LIMIT ?)",
                (overflow,),
            )
    except Exception as e:
        print(f"[ActivityFeed] Erreur record: {e}")

    # Pousser vers les clients SSE connectes
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_payload = {
        "event_type": event_type,
        "actor": anonymized_actor,
        "summary": summary,
        "amount_usdc": amount_usdc,
        "chain": chain,
        "detail": detail,
        "created_at": now_iso,
        "relative_time": "just now",
    }
    event_json = json.dumps(event_payload, ensure_ascii=False)

    # Copie de la liste pour eviter les modifications pendant l'iteration
    for q in list(_sse_queues):
        try:
            q.put_nowait(event_json)
        except asyncio.QueueFull:
            pass  # Client trop lent, on skip


# ── Endpoints REST ──

@router.get("")
@router.get("/")
async def get_feed(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str = Query(default=""),
):
    """Retourne les derniers evenements du feed.

    Query params:
    - limit : nombre d'evenements (defaut 50, max 200)
    - offset : pagination
    - event_type : filtre optionnel (ex: 'swap', 'agent_registered')
    """
    await _ensure_schema()

    try:
        from database import db

        if event_type:
            rows = await db.raw_execute_fetchall(
                "SELECT id, event_type, actor, summary, amount_usdc, chain, detail, created_at "
                "FROM activity_feed WHERE event_type = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (event_type, limit, offset),
            )
        else:
            rows = await db.raw_execute_fetchall(
                "SELECT id, event_type, actor, summary, amount_usdc, chain, detail, created_at "
                "FROM activity_feed ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )

        events = []
        for r in rows:
            events.append({
                "id": r["id"],
                "event_type": r["event_type"],
                "actor": r["actor"],
                "summary": r["summary"],
                "amount_usdc": float(r["amount_usdc"]),
                "chain": r["chain"],
                "detail": r["detail"],
                "created_at": r["created_at"],
                "relative_time": _relative_time(r["created_at"]),
            })

        return {"events": events, "count": len(events), "limit": limit, "offset": offset}

    except Exception as e:
        err = safe_error(e, "activity_feed_list")
        return {"events": [], "count": 0, "error": err["error"], "request_id": err["request_id"]}


@router.get("/stream")
async def feed_stream(request: Request):
    """SSE endpoint — les clients recoivent les evenements en temps reel.

    Connexion : GET /api/feed/stream
    Format : data: {"event_type": "swap", "summary": "...", ...}\n\n
    Heartbeat toutes les 30s pour maintenir la connexion.
    Max 50 clients SSE simultanes.
    """
    await _ensure_schema()

    if len(_sse_queues) >= _MAX_SSE_CLIENTS:
        return {"error": "Too many SSE connections", "max": _MAX_SSE_CLIENTS}

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _sse_queues.append(queue)

    async def event_generator():
        try:
            while True:
                # Verifier si le client est toujours connecte
                if await request.is_disconnected():
                    break

                try:
                    # Attendre un evenement avec timeout de 30s (heartbeat)
                    event_json = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {event_json}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat pour maintenir la connexion
                    yield f": heartbeat {int(time.time())}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Nettoyer la queue a la deconnexion
            if queue in _sse_queues:
                _sse_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stats")
async def feed_stats():
    """Stats agregees du feed sur les dernieres 24h.

    Retourne :
    - total_events : nombre total d'evenements
    - total_volume_usdc : volume total en USDC
    - events_by_type : {swap: 5, agent_registered: 2, ...}
    - active_chains : ["solana", "base", ...]
    - most_active_agent : l'agent le plus actif (anonymise)
    - connected_sse_clients : nombre de clients SSE connectes
    """
    await _ensure_schema()

    try:
        from database import db

        # Cutoff 24h en ISO
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # Total evenements et volume 24h
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_usdc), 0) AS vol "
            "FROM activity_feed WHERE created_at >= ?",
            (cutoff_iso,),
        )
        total_events = rows[0]["cnt"] if rows else 0
        total_volume = float(rows[0]["vol"]) if rows else 0.0

        # Evenements par type
        type_rows = await db.raw_execute_fetchall(
            "SELECT event_type, COUNT(*) AS cnt "
            "FROM activity_feed WHERE created_at >= ? "
            "GROUP BY event_type ORDER BY cnt DESC",
            (cutoff_iso,),
        )
        events_by_type = {r["event_type"]: r["cnt"] for r in type_rows}

        # Chains actives
        chain_rows = await db.raw_execute_fetchall(
            "SELECT DISTINCT chain FROM activity_feed "
            "WHERE created_at >= ? AND chain != '' ORDER BY chain",
            (cutoff_iso,),
        )
        active_chains = [r["chain"] for r in chain_rows]

        # Agent le plus actif
        actor_rows = await db.raw_execute_fetchall(
            "SELECT actor, COUNT(*) AS cnt "
            "FROM activity_feed WHERE created_at >= ? AND actor != '' "
            "GROUP BY actor ORDER BY cnt DESC LIMIT 1",
            (cutoff_iso,),
        )
        most_active = actor_rows[0]["actor"] if actor_rows else ""

        return {
            "total_events": total_events,
            "total_volume_usdc": total_volume,
            "events_by_type": events_by_type,
            "active_chains": active_chains,
            "most_active_agent": most_active,
            "connected_sse_clients": len(_sse_queues),
        }

    except Exception as e:
        err = safe_error(e, "activity_feed_stats")
        return {
            "total_events": 0,
            "total_volume_usdc": 0.0,
            "events_by_type": {},
            "active_chains": [],
            "most_active_agent": "",
            "connected_sse_clients": len(_sse_queues),
            "error": err["error"],
            "request_id": err["request_id"],
        }
