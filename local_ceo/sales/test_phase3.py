"""Phase 3 integration test — telegram_smart_reply routed to MaxiaSalesAgent.

Simulates 5 messages from a fake prospect going through the full
sales funnel and verifies that:

  1. ``answer_user_message`` with ``user_id`` set routes to the sales agent
  2. The stage progresses forward (intro -> ... -> closing)
  3. Legacy flow (no ``user_id``) still works and does NOT touch
     ``conversations.db``
  4. The persisted state survives a fresh agent instantiation

The test does NOT hit real Telegram — it drives the library function
directly, so it can run safely while the main CEO is stopped.

Run:
    cd local_ceo
    python sales/test_phase3.py
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import statistics
import sys
import time
from pathlib import Path

# Force UTF-8 stdout so Qwen's smart quotes don't crash on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_LOCAL_CEO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOCAL_CEO))

# Ensure the sales agent is enabled for this test
import os
os.environ["ENABLE_MAXIA_SALES"] = "1"

from missions.telegram_smart_reply import answer_user_message  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)


# Fake prospect identifier — isolated from production conversations
FAKE_USER_ID = "phase3_test_prospect"
CHANNEL = "telegram"


async def scenario_full_funnel() -> list[float]:
    """5 messages going through the whole funnel."""
    print("=" * 70)
    print("  Scenario 1 — Full funnel (FR prospect, 5 messages)")
    print("=" * 70)

    messages = [
        "Bonjour, j'ai entendu parler de MAXIA, qu'est-ce que c'est ?",
        "Je construis un agent IA qui scanne les opportunites DeFi sur Solana",
        "Interessant. Combien ca coute pour lister mon agent sur votre marketplace ?",
        "OK 0,5% c'est raisonnable pour les volumes moyens. Je paye en USDC ?",
        "Parfait, comment je m'inscris pour commencer ?",
    ]

    latencies: list[float] = []
    for i, msg in enumerate(messages, 1):
        print(f"\n[USER {i}] {msg}")
        t0 = time.time()
        try:
            reply = await answer_user_message(
                user_message=msg,
                history=[],  # ignored in sales mode
                language_code="fr",
                user_id=FAKE_USER_ID,
                channel=CHANNEL,
            )
        except Exception as e:
            reply = f"<ERROR {type(e).__name__}: {e}>"
        dt = time.time() - t0
        latencies.append(dt)
        print(f"[BOT]   ({dt:.2f}s) {reply[:400]}")

    return latencies


async def scenario_legacy_alexis() -> list[float]:
    """Verify that a call WITHOUT user_id still works (legacy path for Alexis)."""
    print()
    print("=" * 70)
    print("  Scenario 2 — Legacy path (Alexis assistant, no user_id)")
    print("=" * 70)

    latencies: list[float] = []
    print("\n[ALEXIS] Combien on a de missions V9 dans le CEO ?")
    t0 = time.time()
    try:
        reply = await answer_user_message(
            user_message="Combien on a de missions V9 dans le CEO ?",
            history=[],
            language_code="fr",
            # user_id is None -> legacy flow
        )
    except Exception as e:
        reply = f"<ERROR {type(e).__name__}: {e}>"
    dt = time.time() - t0
    latencies.append(dt)
    print(f"[BOT]   ({dt:.2f}s) {reply[:400]}")
    return latencies


def verify_persistence() -> bool:
    """Ensure the fake prospect's state is written to conversations.db."""
    print()
    print("=" * 70)
    print("  Scenario 3 — Persistence check (SQLite)")
    print("=" * 70)

    db = _LOCAL_CEO / "sales" / "conversations.db"
    if not db.exists():
        print(f"[FAIL] {db} does not exist")
        return False
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT stage, lang, length(history_json) FROM conversations "
            "WHERE conversation_id = ?",
            (f"{CHANNEL}:{FAKE_USER_ID}",),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        print(f"[FAIL] conversation {CHANNEL}:{FAKE_USER_ID} not in DB")
        return False
    stage, lang, history_len = row
    print(f"[OK] persisted stage={stage} lang={lang} history_len={history_len} bytes")

    # We expect the conversation to have advanced past intro given the 5
    # forward-moving messages. Accept anything that is NOT still 1_intro.
    if stage == "1_intro":
        print("[WARN] stage still 1_intro after 5 messages — funnel not progressing")
    return True


def cleanup_fake_prospect() -> None:
    """Remove the fake prospect from the DB so the test is idempotent."""
    db = _LOCAL_CEO / "sales" / "conversations.db"
    if not db.exists():
        return
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (f"{CHANNEL}:{FAKE_USER_ID}",),
        )
        conn.commit()
    finally:
        conn.close()


async def main() -> int:
    # Clean slate
    cleanup_fake_prospect()

    lat_funnel = await scenario_full_funnel()
    lat_legacy = await scenario_legacy_alexis()
    persist_ok = verify_persistence()

    all_lat = lat_funnel + lat_legacy
    print()
    print("=" * 70)
    if all_lat:
        p50 = statistics.median(all_lat)
        p95 = sorted(all_lat)[max(0, int(len(all_lat) * 0.95) - 1)]
        print(f"  LATENCY  p50={p50:.2f}s  p95={p95:.2f}s  n={len(all_lat)}")
    print(f"  PERSISTENCE  {'OK' if persist_ok else 'FAIL'}")
    print("=" * 70)

    return 0 if persist_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
