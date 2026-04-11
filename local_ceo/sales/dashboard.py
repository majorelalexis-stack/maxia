"""MaxiaSalesAgent observability dashboard.

Reads ``sales/conversations.db`` and prints a human-friendly summary of
the sales agent activity:

  - Active conversations (last seen <24h)
  - Total conversations ever
  - Stage breakdown (how many in intro vs qualification vs closing)
  - Per-channel breakdown (telegram, email, discord, forum)
  - Per-language breakdown
  - Funnel conversion (% of conversations that ever reached closing)
  - Latency p50 / p95 over the last 7 days
  - Token throughput

Two entry points:

- ``main()`` — CLI for ``python sales/dashboard.py``
- ``snapshot()`` — returns a dict of metrics, used by mission_weekly_report
  to embed sales stats in the email Alexis gets every Monday.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
DEFAULT_DB = _HERE / "conversations.db"


def _open(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def snapshot(db_path: Path = DEFAULT_DB, window_days: int = 7) -> dict:
    """Return a dict of all sales metrics. Safe for embedding in reports.

    The returned dict is JSON-serializable so it can be dumped into a
    weekly email or pretty-printed by the CLI.
    """
    now = time.time()
    cutoff = now - (window_days * 86400)
    active_cutoff = now - 86400  # 24h

    metrics: dict = {
        "as_of": int(now),
        "db_path": str(db_path),
        "window_days": window_days,
        "ok": False,
    }

    conn = _open(db_path)
    if conn is None:
        metrics["error"] = f"db not found: {db_path}"
        return metrics

    try:
        # ── Conversations table ──
        all_rows = conn.execute(
            "SELECT conversation_id, channel, user_id, stage, lang, "
            "last_seen_at, created_at, history_json "
            "FROM conversations"
        ).fetchall()

        total = len(all_rows)
        active = sum(1 for r in all_rows if (r["last_seen_at"] or 0) >= active_cutoff)
        recent = sum(1 for r in all_rows if (r["created_at"] or 0) >= cutoff)

        stage_counts: dict[str, int] = {}
        channel_counts: dict[str, int] = {}
        lang_counts: dict[str, int] = {}
        for r in all_rows:
            stage_counts[r["stage"]] = stage_counts.get(r["stage"], 0) + 1
            channel_counts[r["channel"]] = channel_counts.get(r["channel"], 0) + 1
            lang_counts[r["lang"]] = lang_counts.get(r["lang"], 0) + 1

        closing_now = stage_counts.get("6_closing", 0)
        funnel_conversion_pct = (closing_now / total * 100) if total else 0.0

        # ── turns_log table ──
        turn_rows = conn.execute(
            "SELECT role, latency_ms, tokens_in, tokens_out, ts "
            "FROM turns_log WHERE ts >= ?",
            (cutoff,),
        ).fetchall()

        bot_lats = [int(r["latency_ms"]) for r in turn_rows if r["role"] == "bot" and r["latency_ms"]]
        total_tokens_in = sum(int(r["tokens_in"] or 0) for r in turn_rows)
        total_tokens_out = sum(int(r["tokens_out"] or 0) for r in turn_rows)
        bot_turns = sum(1 for r in turn_rows if r["role"] == "bot")
        user_turns = sum(1 for r in turn_rows if r["role"] == "user")

        if bot_lats:
            sorted_lats = sorted(bot_lats)
            p50 = statistics.median(sorted_lats) / 1000.0
            idx95 = max(0, int(len(sorted_lats) * 0.95) - 1)
            p95 = sorted_lats[idx95] / 1000.0
            mean = statistics.mean(sorted_lats) / 1000.0
        else:
            p50 = p95 = mean = 0.0

        # ── Active conversations list (top 10 by last_seen, with preview) ──
        active_list: list[dict] = []
        sorted_rows = sorted(
            all_rows,
            key=lambda r: r["last_seen_at"] or 0,
            reverse=True,
        )[:10]
        for r in sorted_rows:
            preview = ""
            try:
                hist = json.loads(r["history_json"] or "[]")
                if hist:
                    last_user = next(
                        (h for h in reversed(hist) if h.get("role") == "user"),
                        None,
                    )
                    if last_user:
                        preview = (last_user.get("content") or "")[:120]
            except Exception:
                pass
            active_list.append({
                "id": r["conversation_id"],
                "channel": r["channel"],
                "user_id": r["user_id"],
                "stage": r["stage"],
                "lang": r["lang"],
                "last_seen_at": int(r["last_seen_at"] or 0),
                "preview": preview,
            })

        metrics.update({
            "ok": True,
            "totals": {
                "all_time_conversations": total,
                "active_24h": active,
                "new_in_window": recent,
                "currently_in_closing": closing_now,
                "funnel_conversion_pct": round(funnel_conversion_pct, 1),
            },
            "stages": dict(sorted(stage_counts.items())),
            "channels": dict(sorted(channel_counts.items())),
            "languages": dict(sorted(lang_counts.items())),
            "turns_window": {
                "user_turns": user_turns,
                "bot_turns": bot_turns,
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "latency_p50_s": round(p50, 2),
                "latency_p95_s": round(p95, 2),
                "latency_mean_s": round(mean, 2),
            },
            "active_list": active_list,
        })
    finally:
        conn.close()

    return metrics


def get_conversation(conversation_id: str, db_path: Path = DEFAULT_DB) -> dict:
    """Return the full history + telemetry for a single conversation.

    Used by the main dashboard's modal "view conversation" feature so the
    user can click on an active prospect and see every turn.
    """
    out: dict = {"ok": False, "conversation_id": conversation_id}
    conn = _open(db_path)
    if conn is None:
        out["error"] = "db not found"
        return out
    try:
        row = conn.execute(
            "SELECT conversation_id, channel, user_id, stage, lang, "
            "history_json, summary, created_at, last_seen_at "
            "FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            out["error"] = "not found"
            return out

        try:
            history = json.loads(row["history_json"] or "[]")
        except Exception:
            history = []

        # Pull turns_log telemetry too (latency per turn)
        log_rows = conn.execute(
            "SELECT turn_idx, role, latency_ms, tokens_in, tokens_out, ts "
            "FROM turns_log WHERE conversation_id = ? ORDER BY turn_idx",
            (conversation_id,),
        ).fetchall()
        telemetry = [dict(r) for r in log_rows]

        out.update({
            "ok": True,
            "channel": row["channel"],
            "user_id": row["user_id"],
            "stage": row["stage"],
            "lang": row["lang"],
            "summary": row["summary"] or "",
            "created_at": int(row["created_at"] or 0),
            "last_seen_at": int(row["last_seen_at"] or 0),
            "history": history,
            "telemetry": telemetry,
        })
    finally:
        conn.close()
    return out


def format_text(metrics: dict) -> str:
    """Pretty-print a metrics dict for terminals and emails."""
    if not metrics.get("ok"):
        return f"[sales dashboard] error: {metrics.get('error', 'unknown')}"

    t = metrics["totals"]
    tw = metrics["turns_window"]
    win = metrics.get("window_days", 7)

    lines: list[str] = []
    lines.append("=" * 64)
    lines.append("  MaxiaSalesAgent dashboard")
    lines.append("=" * 64)
    lines.append("")
    lines.append("CONVERSATIONS")
    lines.append(f"  all-time             : {t['all_time_conversations']}")
    lines.append(f"  active (last 24h)    : {t['active_24h']}")
    lines.append(f"  new (last {win}d)        : {t['new_in_window']}")
    lines.append(f"  currently in closing : {t['currently_in_closing']}")
    lines.append(f"  funnel conversion    : {t['funnel_conversion_pct']}%")
    lines.append("")

    if metrics["stages"]:
        lines.append("STAGE BREAKDOWN")
        for stage, count in metrics["stages"].items():
            bar = "#" * min(40, count)
            lines.append(f"  {stage:24} {count:4}  {bar}")
        lines.append("")

    if metrics["channels"]:
        lines.append("CHANNELS")
        for ch, count in metrics["channels"].items():
            lines.append(f"  {ch:12} {count}")
        lines.append("")

    if metrics["languages"]:
        lines.append("LANGUAGES")
        for lang, count in metrics["languages"].items():
            lines.append(f"  {lang:5} {count}")
        lines.append("")

    lines.append(f"TURNS (last {win}d)")
    lines.append(f"  user turns           : {tw['user_turns']}")
    lines.append(f"  bot turns            : {tw['bot_turns']}")
    lines.append(f"  tokens in (approx)   : {tw['total_tokens_in']}")
    lines.append(f"  tokens out (approx)  : {tw['total_tokens_out']}")
    lines.append("")
    lines.append(f"LATENCY (bot generation, last {win}d)")
    lines.append(f"  p50 = {tw['latency_p50_s']}s")
    lines.append(f"  p95 = {tw['latency_p95_s']}s")
    lines.append(f"  mean = {tw['latency_mean_s']}s")
    lines.append("=" * 64)
    return "\n".join(lines)


def main() -> int:
    metrics = snapshot()
    if "--json" in sys.argv:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print(format_text(metrics))
    return 0 if metrics.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
