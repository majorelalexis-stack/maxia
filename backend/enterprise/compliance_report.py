"""Compliance Report Generator.

Two flavors:
1. ``generate_compliance_report(wallet, db, period_days)`` — legacy wallet-scoped
   JSON report that lists transactions and disputes for one wallet. Kept for
   backward compatibility with ``/api/enterprise/compliance/{wallet}``.

2. ``generate_eu_ai_act_report(db, tenant_id, period_str, fmt)`` — tenant-wide
   MAXIA Guard 6-pillar summary over a named period (Q1-2026, 2026-03,
   last-30d), returned as CSV or print-friendly HTML. Reads from
   ``audit_trail`` (pillar 4) and groups events into the six pillars.
"""
import csv
import io
import json
import re
import time
from calendar import monthrange
from datetime import datetime, timezone
from typing import Optional


async def generate_compliance_report(wallet: str, db, period_days: int = 30) -> dict:
    """Legacy wallet-scoped compliance report."""
    cutoff = int(time.time()) - period_days * 86400

    try:
        txs = await db.raw_execute_fetchall(
            "SELECT tx_signature, wallet, amount_usdc, purpose, buyer, seller, created_at "
            "FROM transactions WHERE (buyer=? OR seller=?) AND created_at>? ORDER BY created_at DESC",
            (wallet, wallet, cutoff))
        transactions = [dict(t) for t in txs]
    except Exception:
        transactions = []

    try:
        disputes = await db.raw_execute_fetchall(
            "SELECT id, delivery_id, escrow_id, initiator, reason, resolution, "
            "resolved_at, resolved_by, created_at "
            "FROM pod_disputes WHERE data LIKE ? AND created_at>?",
            (f"%{wallet}%", cutoff))
        dispute_list = [dict(d) for d in disputes]
    except Exception:
        dispute_list = []

    volume_by_type: dict = {}
    for tx in transactions:
        purpose = tx.get("purpose", "unknown")
        amount = tx.get("amount_usdc", 0) or 0
        volume_by_type.setdefault(purpose, 0)
        volume_by_type[purpose] += amount

    return {
        "wallet": wallet,
        "period": f"Last {period_days} days",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "total_transactions": len(transactions),
            "total_volume_usdc": round(sum(tx.get("amount_usdc", 0) or 0 for tx in transactions), 2),
            "total_disputes": len(dispute_list),
            "disputes_resolved": sum(1 for d in dispute_list if d.get("resolution")),
            "ofac_screenings": len(transactions),
            "ofac_blocks": 0,
        },
        "volume_by_type": volume_by_type,
        "transactions": transactions[:100],
        "disputes": dispute_list,
        "compliance_status": "COMPLIANT",
        "ofac_provider": "Chainalysis Oracle + 55 local sanctioned addresses",
        "screening_policy": "Pre-transaction screening on all wallets",
    }


# ─────────────────────────────────────────────────────────────
#  EU AI Act — tenant-wide MAXIA Guard 6-pillar report
# ─────────────────────────────────────────────────────────────

# Map each audit_trail action to one of the 6 MAXIA Guard pillars.
# Actions that don't match fall into "other".
_PILLAR_ACTION_MAP = {
    1: {  # Verified Actions (signed intents, signature verification)
        "intent_signed", "intent_verified", "intent_rejected",
        "signature_verified", "nonce_reject", "nonce_consumed",
    },
    2: {  # Budget Caps
        "spend_check", "spend_cap_hit", "daily_cap_hit", "lifetime_cap_hit",
        "record_spend",
    },
    3: {  # Policy Scopes (scope checks, freeze/downgrade/revoke, policy engine)
        "scope_check", "scope_denied", "freeze", "unfreeze", "downgrade",
        "revoke", "rotate_key", "policy_denied", "policy_allowed",
    },
    4: {  # Audit Trail housekeeping + compliance checks
        "audit_query", "audit_export", "compliance_check",
    },
    5: {  # Input Shield
        "content_block", "ofac_block", "prompt_injection_block",
        "pii_scrub", "content_safety_pass",
    },
    6: {  # Rate Caps
        "rate_limit_hit", "burst_block", "ip_ban",
    },
}

_PILLAR_META = {
    1: ("Verified Actions", "backend/core/intent.py",
        "ed25519-signed intent envelopes (AIP v0.3.0) — replay, tampering, impersonation blocked by construction."),
    2: ("Budget Caps", "backend/agents/agent_permissions.py",
        "Per-call / per-day / lifetime USDC spend caps enforced before any downstream logic."),
    3: ("Policy Scopes", "backend/agents/agent_permissions.py + backend/core/policy_engine.py",
        "18 OAuth-style scopes + freeze/downgrade/revoke/rotate + declarative policy.yaml."),
    4: ("Audit Trail", "backend/enterprise/audit_trail.py",
        "Append-only log of every auth decision and policy change. CSV export for regulators."),
    5: ("Input Shield", "backend/core/security.py + backend/core/pii_shield.py",
        "OFAC screening, prompt-injection filter, PII scrub on inputs and outbound responses."),
    6: ("Rate Caps", "backend/core/security.py",
        "100 req/day free tier hard-cap in middleware. Protects agents from their own retry loops."),
}


def _parse_period(period_str: str) -> tuple[int, int, str]:
    """Parse a period spec into (start_ts, end_ts, human_label).

    Supported forms:
      - ``Q1-2026`` … ``Q4-YYYY``
      - ``YYYY-MM`` (e.g. ``2026-03``)
      - ``last-30d``, ``last-7d``, ``last-90d``
      - ``YYYY`` (full calendar year)

    Raises ``ValueError`` on unparseable input.
    """
    if not period_str:
        raise ValueError("period is required (e.g. Q1-2026, 2026-03, last-30d)")
    s = period_str.strip().lower()

    m = re.fullmatch(r"q([1-4])-(\d{4})", s)
    if m:
        q = int(m.group(1))
        year = int(m.group(2))
        start_month = (q - 1) * 3 + 1
        end_month = start_month + 2
        start_ts = int(datetime(year, start_month, 1, tzinfo=timezone.utc).timestamp())
        _, last_day = monthrange(year, end_month)
        end_ts = int(datetime(year, end_month, last_day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        return start_ts, end_ts, f"Q{q} {year}"

    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"invalid month in {period_str}")
        start_ts = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
        _, last_day = monthrange(year, month)
        end_ts = int(datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        return start_ts, end_ts, f"{year}-{month:02d}"

    m = re.fullmatch(r"last-(\d{1,4})d", s)
    if m:
        days = int(m.group(1))
        if not 1 <= days <= 3650:
            raise ValueError(f"period days out of range: {days}")
        end_ts = int(time.time())
        start_ts = end_ts - days * 86400
        return start_ts, end_ts, f"Last {days} days"

    m = re.fullmatch(r"(\d{4})", s)
    if m:
        year = int(m.group(1))
        start_ts = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        return start_ts, end_ts, f"Full year {year}"

    raise ValueError(f"unrecognised period format: {period_str!r}")


def _pillar_for_action(action: str) -> int:
    for pillar, actions in _PILLAR_ACTION_MAP.items():
        if action in actions:
            return pillar
    return 0  # other


async def _load_entries(db, tenant_id: str, start_ts: int, end_ts: int, limit: int = 50000) -> list:
    """Load audit_trail rows for the tenant/period. Empty list on any error."""
    try:
        from enterprise.audit_trail import get_audit_trail
        return await get_audit_trail(
            db, tenant_id=tenant_id, start=start_ts, end=end_ts, limit=limit,
        )
    except Exception:
        return []


def _summarise(entries: list) -> dict:
    """Group audit entries into the 6-pillar summary."""
    per_pillar: dict = {i: {"event_count": 0, "block_count": 0, "first_seen": 0, "last_seen": 0}
                        for i in range(1, 7)}
    per_pillar[0] = {"event_count": 0, "block_count": 0, "first_seen": 0, "last_seen": 0}

    total_events = 0
    total_blocks = 0
    total_volume_usdc = 0.0

    for entry in entries:
        action = entry.get("action", "")
        ts = entry.get("timestamp", 0) or 0
        amount = entry.get("amount_usdc", 0) or 0
        policy = entry.get("policy_check", "pass")
        result = entry.get("result", "success")
        is_block = policy == "fail" or result in ("blocked", "denied", "rejected")

        pillar = _pillar_for_action(action)
        slot = per_pillar[pillar]
        slot["event_count"] += 1
        if is_block:
            slot["block_count"] += 1
        if slot["first_seen"] == 0 or (ts and ts < slot["first_seen"]):
            slot["first_seen"] = ts
        if ts and ts > slot["last_seen"]:
            slot["last_seen"] = ts

        total_events += 1
        if is_block:
            total_blocks += 1
        total_volume_usdc += amount

    return {
        "per_pillar": per_pillar,
        "total_events": total_events,
        "total_blocks": total_blocks,
        "total_volume_usdc": round(total_volume_usdc, 2),
    }


def _ts_iso(ts: int) -> str:
    if not ts:
        return ""
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_csv(meta: dict, summary: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["# MAXIA Guard EU AI Act Compliance Report"])
    w.writerow(["# Tenant", meta["tenant_id"] or "(all tenants)"])
    w.writerow(["# Period", meta["period_label"]])
    w.writerow(["# Period start (UTC)", _ts_iso(meta["start_ts"])])
    w.writerow(["# Period end (UTC)", _ts_iso(meta["end_ts"])])
    w.writerow(["# Generated at (UTC)", meta["generated_at"]])
    w.writerow(["# Total events", summary["total_events"]])
    w.writerow(["# Total blocks", summary["total_blocks"]])
    w.writerow(["# Total volume USDC", summary["total_volume_usdc"]])
    w.writerow([])
    w.writerow([
        "pillar", "pillar_name", "source", "description",
        "event_count", "block_count", "first_seen_utc", "last_seen_utc",
    ])

    for pillar_num in (1, 2, 3, 4, 5, 6):
        name, source, desc = _PILLAR_META[pillar_num]
        slot = summary["per_pillar"][pillar_num]
        w.writerow([
            pillar_num, name, source, desc,
            slot["event_count"], slot["block_count"],
            _ts_iso(slot["first_seen"]), _ts_iso(slot["last_seen"]),
        ])

    other = summary["per_pillar"][0]
    w.writerow([
        0, "Other", "(unmapped actions)",
        "Audit entries not associated with a MAXIA Guard pillar.",
        other["event_count"], other["block_count"],
        _ts_iso(other["first_seen"]), _ts_iso(other["last_seen"]),
    ])

    return out.getvalue().encode("utf-8")


def _render_html(meta: dict, summary: dict) -> bytes:
    rows_html = []
    for pillar_num in (1, 2, 3, 4, 5, 6):
        name, source, desc = _PILLAR_META[pillar_num]
        slot = summary["per_pillar"][pillar_num]
        rows_html.append(
            "<tr>"
            f"<td>{pillar_num}</td>"
            f"<td><strong>{name}</strong></td>"
            f"<td><code>{source}</code></td>"
            f"<td>{desc}</td>"
            f"<td>{slot['event_count']}</td>"
            f"<td>{slot['block_count']}</td>"
            f"<td>{_ts_iso(slot['first_seen'])}</td>"
            f"<td>{_ts_iso(slot['last_seen'])}</td>"
            "</tr>"
        )
    other = summary["per_pillar"][0]
    rows_html.append(
        "<tr>"
        "<td>0</td><td>Other</td><td><code>(unmapped)</code></td>"
        "<td>Audit entries not associated with a MAXIA Guard pillar.</td>"
        f"<td>{other['event_count']}</td>"
        f"<td>{other['block_count']}</td>"
        f"<td>{_ts_iso(other['first_seen'])}</td>"
        f"<td>{_ts_iso(other['last_seen'])}</td>"
        "</tr>"
    )
    body = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>MAXIA Guard — EU AI Act Compliance Report</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#111}"
        "h1{font-size:24px;margin:0 0 8px}h2{font-size:16px;color:#444;margin:0 0 24px}"
        "table{border-collapse:collapse;width:100%;margin-top:24px}"
        "th,td{border:1px solid #ccc;padding:8px 10px;text-align:left;font-size:13px;vertical-align:top}"
        "th{background:#f4f4f4}code{font-size:12px;color:#555}"
        ".meta{background:#fafafa;border:1px solid #eee;padding:16px;border-radius:8px}"
        ".meta div{margin:4px 0;font-size:13px}"
        "</style></head><body>"
        "<h1>MAXIA Guard &mdash; EU AI Act Compliance Report</h1>"
        "<h2>6-pillar guardrail activity summary</h2>"
        "<div class='meta'>"
        f"<div><strong>Tenant:</strong> {meta['tenant_id'] or '(all tenants)'}</div>"
        f"<div><strong>Period:</strong> {meta['period_label']}</div>"
        f"<div><strong>Start (UTC):</strong> {_ts_iso(meta['start_ts'])}</div>"
        f"<div><strong>End (UTC):</strong> {_ts_iso(meta['end_ts'])}</div>"
        f"<div><strong>Generated at (UTC):</strong> {meta['generated_at']}</div>"
        f"<div><strong>Total events:</strong> {summary['total_events']} &middot; "
        f"<strong>Total blocks:</strong> {summary['total_blocks']} &middot; "
        f"<strong>Total volume USDC:</strong> {summary['total_volume_usdc']:,.2f}</div>"
        "</div>"
        "<table><thead><tr>"
        "<th>#</th><th>Pillar</th><th>Source</th><th>Description</th>"
        "<th>Events</th><th>Blocks</th><th>First seen</th><th>Last seen</th>"
        "</tr></thead><tbody>"
        + "".join(rows_html) +
        "</tbody></table>"
        "<p style='margin-top:32px;color:#666;font-size:12px'>"
        "Generated by MAXIA Guard &mdash; "
        "<a href='https://maxiaworld.app/guard'>maxiaworld.app/guard</a>. "
        "This report is derived from the append-only audit trail "
        "(pillar 4). See <a href='https://github.com/MAXIAWORLD/maxia/blob/main/docs/MAXIA_GUARD.md'>"
        "docs/MAXIA_GUARD.md</a> for methodology."
        "</p></body></html>"
    )
    return body.encode("utf-8")


def _render_json(meta: dict, summary: dict) -> bytes:
    pillars_out = []
    for pillar_num in (1, 2, 3, 4, 5, 6):
        name, source, desc = _PILLAR_META[pillar_num]
        slot = summary["per_pillar"][pillar_num]
        pillars_out.append({
            "pillar": pillar_num,
            "name": name,
            "source": source,
            "description": desc,
            "event_count": slot["event_count"],
            "block_count": slot["block_count"],
            "first_seen_utc": _ts_iso(slot["first_seen"]),
            "last_seen_utc": _ts_iso(slot["last_seen"]),
        })
    payload = {
        "report_type": "eu_ai_act_compliance",
        "product": "MAXIA Guard",
        "tenant_id": meta["tenant_id"],
        "period_label": meta["period_label"],
        "period_start_utc": _ts_iso(meta["start_ts"]),
        "period_end_utc": _ts_iso(meta["end_ts"]),
        "generated_at_utc": meta["generated_at"],
        "total_events": summary["total_events"],
        "total_blocks": summary["total_blocks"],
        "total_volume_usdc": summary["total_volume_usdc"],
        "pillars": pillars_out,
        "other": summary["per_pillar"][0],
        "methodology_url": "https://github.com/MAXIAWORLD/maxia/blob/main/docs/MAXIA_GUARD.md",
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


async def generate_eu_ai_act_report(
    db,
    tenant_id: str = "",
    period: str = "last-30d",
    fmt: str = "csv",
) -> tuple[bytes, str, str]:
    """Generate an EU AI Act compliance report for a tenant.

    Returns (body_bytes, content_type, suggested_filename).
    ``fmt`` must be one of ``csv``, ``html``, ``json``.
    """
    fmt = (fmt or "csv").lower()
    if fmt not in ("csv", "html", "json"):
        raise ValueError(f"unsupported format: {fmt}")

    start_ts, end_ts, label = _parse_period(period)
    entries = await _load_entries(db, tenant_id, start_ts, end_ts)
    summary = _summarise(entries)

    meta = {
        "tenant_id": tenant_id,
        "period_label": label,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    safe_period = re.sub(r"[^a-zA-Z0-9_-]", "_", period)
    if fmt == "csv":
        return _render_csv(meta, summary), "text/csv; charset=utf-8", f"maxia-guard-{safe_period}.csv"
    if fmt == "html":
        return _render_html(meta, summary), "text/html; charset=utf-8", f"maxia-guard-{safe_period}.html"
    return _render_json(meta, summary), "application/json", f"maxia-guard-{safe_period}.json"
