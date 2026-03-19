"""MAXIA SLA Manager — SLA & Quality Guarantee (Service Level Agreements)"""
import uuid, time, json


SLA_TABLES = (
    "CREATE TABLE IF NOT EXISTS service_sla ("
    "service_id TEXT PRIMARY KEY, max_response_time_s INTEGER NOT NULL DEFAULT 30,"
    "min_quality_rating REAL NOT NULL DEFAULT 3.0,"
    "guarantee_refund_pct REAL NOT NULL DEFAULT 100,"
    "auto_refund_enabled INTEGER NOT NULL DEFAULT 1,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS service_ratings ("
    "id TEXT PRIMARY KEY, service_id TEXT NOT NULL, buyer_wallet TEXT NOT NULL,"
    "rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),"
    "comment TEXT DEFAULT '', tx_id TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_ratings_service ON service_ratings(service_id);"
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ratings_buyer_tx ON service_ratings(buyer_wallet, tx_id);"

    "CREATE TABLE IF NOT EXISTS sla_violations ("
    "id TEXT PRIMARY KEY, service_id TEXT NOT NULL, violation_type TEXT NOT NULL,"
    "details TEXT DEFAULT '', detected_at INTEGER DEFAULT (strftime('%s','now')),"
    "refund_tx TEXT DEFAULT '', refund_amount_usdc REAL DEFAULT 0);"

    "CREATE INDEX IF NOT EXISTS idx_violations_service ON sla_violations(service_id);"
)


async def ensure_tables(db):
    """Create SLA tables if they don't exist."""
    try:
        await db._db.executescript(SLA_TABLES)
        await db._db.commit()
        print("[SLA] Tables initialisees")
    except Exception as e:
        print(f"[SLA] Erreur creation tables: {e}")


async def set_sla(db, service_id: str, config_dict: dict) -> dict:
    """Seller sets SLA for a service."""
    max_response = int(config_dict.get("max_response_time_s", 30))
    min_quality = float(config_dict.get("min_quality_rating", 3.0))
    refund_pct = float(config_dict.get("guarantee_refund_pct", 100))
    auto_refund = 1 if config_dict.get("auto_refund_enabled", True) else 0

    if not (1.0 <= min_quality <= 5.0):
        return {"success": False, "error": "min_quality_rating doit etre entre 1.0 et 5.0"}
    if not (0 <= refund_pct <= 100):
        return {"success": False, "error": "guarantee_refund_pct doit etre entre 0 et 100"}
    if max_response < 1:
        return {"success": False, "error": "max_response_time_s doit etre >= 1"}

    await db._db.execute(
        "INSERT OR REPLACE INTO service_sla(service_id, max_response_time_s, min_quality_rating, "
        "guarantee_refund_pct, auto_refund_enabled) VALUES(?,?,?,?,?)",
        (service_id, max_response, min_quality, refund_pct, auto_refund))
    await db._db.commit()

    return {
        "success": True,
        "service_id": service_id,
        "max_response_time_s": max_response,
        "min_quality_rating": min_quality,
        "guarantee_refund_pct": refund_pct,
        "auto_refund_enabled": bool(auto_refund),
    }


async def get_sla(db, service_id: str) -> dict:
    """Returns SLA config for a service, or defaults if not set."""
    rows = await db._db.execute_fetchall(
        "SELECT * FROM service_sla WHERE service_id=?", (service_id,))
    if rows:
        row = dict(rows[0])
        row["auto_refund_enabled"] = bool(row["auto_refund_enabled"])
        return row
    return {
        "service_id": service_id,
        "max_response_time_s": 30,
        "min_quality_rating": 3.0,
        "guarantee_refund_pct": 100,
        "auto_refund_enabled": True,
        "created_at": None,
        "default": True,
    }


async def check_sla_compliance(db, service_id: str, response_time_s: float) -> dict:
    """Check if a service response complies with its SLA."""
    sla = await get_sla(db, service_id)

    # Check response time
    if response_time_s > sla["max_response_time_s"]:
        return {
            "compliant": False,
            "violation": f"response_time_exceeded: {response_time_s:.1f}s > {sla['max_response_time_s']}s",
        }

    # Check quality rating
    quality = await get_service_quality(db, service_id)
    if quality["total_ratings"] >= 3 and quality["avg_rating"] < sla["min_quality_rating"]:
        return {
            "compliant": False,
            "violation": f"quality_below_threshold: {quality['avg_rating']:.2f} < {sla['min_quality_rating']}",
        }

    return {"compliant": True, "violation": None}


async def auto_refund_sla(db, escrow_client, escrow_id: str, service_id: str, violation: str) -> dict:
    """Trigger escrow refund for SLA violation and record the violation."""
    sla = await get_sla(db, service_id)
    if not sla.get("auto_refund_enabled", True):
        return {"success": False, "error": "Auto-refund desactive pour ce service"}

    # Get escrow details
    escrow = escrow_client.get_escrow(escrow_id)
    if "error" in escrow:
        return {"success": False, "error": escrow["error"]}
    if escrow.get("status") != "locked":
        return {"success": False, "error": f"Escrow status invalide: {escrow.get('status')}"}

    refund_pct = sla["guarantee_refund_pct"] / 100.0
    refund_amount = escrow["amount_usdc"] * refund_pct

    # Trigger the refund via escrow dispute resolution (refund to buyer)
    result = await escrow_client.resolve_dispute(escrow_id=escrow_id, release_to_seller=False)

    violation_id = str(uuid.uuid4())
    refund_tx = result.get("releaseTx", result.get("refundTx", ""))

    await db._db.execute(
        "INSERT INTO sla_violations(id, service_id, violation_type, details, refund_tx, refund_amount_usdc) "
        "VALUES(?,?,?,?,?,?)",
        (violation_id, service_id, violation.split(":")[0] if ":" in violation else violation,
         violation, refund_tx, refund_amount))
    await db._db.commit()

    return {
        "success": result.get("success", False),
        "violation_id": violation_id,
        "violation": violation,
        "refund_amount_usdc": refund_amount,
        "refund_tx": refund_tx,
        "escrow_id": escrow_id,
    }


async def rate_service(db, service_id: str, buyer_wallet: str,
                       rating: int, comment: str, tx_id: str) -> dict:
    """Store a rating (1 per buyer per tx). Returns the saved rating."""
    if not (1 <= rating <= 5):
        return {"success": False, "error": "rating doit etre entre 1 et 5"}

    # Check uniqueness: 1 rating per buyer per tx
    existing = await db._db.execute_fetchall(
        "SELECT id FROM service_ratings WHERE buyer_wallet=? AND tx_id=?",
        (buyer_wallet, tx_id))
    if existing:
        return {"success": False, "error": "Rating deja soumis pour cette transaction"}

    rating_id = str(uuid.uuid4())
    await db._db.execute(
        "INSERT INTO service_ratings(id, service_id, buyer_wallet, rating, comment, tx_id) "
        "VALUES(?,?,?,?,?,?)",
        (rating_id, service_id, buyer_wallet, rating, comment, tx_id))
    await db._db.commit()

    return {
        "success": True,
        "rating_id": rating_id,
        "service_id": service_id,
        "rating": rating,
        "comment": comment,
    }


async def get_service_quality(db, service_id: str) -> dict:
    """Returns quality metrics for a service."""
    # Average rating and total
    row = await db._fetchone(
        "SELECT COALESCE(AVG(rating), 0) AS avg_rating, COUNT(*) AS total_ratings "
        "FROM service_ratings WHERE service_id=?", (service_id,))
    avg_rating = round(float(row["avg_rating"]), 2) if row else 0.0
    total_ratings = int(row["total_ratings"]) if row else 0

    # Violations count
    vrow = await db._fetchone(
        "SELECT COUNT(*) AS cnt FROM sla_violations WHERE service_id=?", (service_id,))
    violations_count = int(vrow["cnt"]) if vrow else 0

    # SLA compliance percentage (based on violations vs total ratings/transactions)
    total_interactions = max(total_ratings, 1)
    sla_compliance_pct = round(
        max(0, (total_interactions - violations_count) / total_interactions * 100), 1)

    return {
        "service_id": service_id,
        "avg_rating": avg_rating,
        "total_ratings": total_ratings,
        "sla_compliance_pct": sla_compliance_pct,
        "violations_count": violations_count,
    }
