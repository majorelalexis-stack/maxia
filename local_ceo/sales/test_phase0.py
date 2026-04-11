"""Phase 0 validation — MaxiaSalesAgent + Qwen3:30b-a3b-instruct-2507.

Tests 3 French dialogues against the MAXIA catalog:
    1. Cold prospect discovery
    2. Price objection
    3. Technical question about MCP

Measures p50/p95 latency and prints the actual LLM output so Alexis
can eyeball factual accuracy.

Run:
    cd local_ceo
    python sales/test_phase0.py
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import sys
import time
from pathlib import Path

# Force UTF-8 on stdout/stderr so unicode chars from the LLM (narrow NBSP,
# smart quotes, curly apostrophes, accented chars) don't crash the Windows
# cp1252 default console encoding.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Make the sales module importable when run as a script
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from sales import MaxiaSalesAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)


async def main() -> int:
    print("=" * 70)
    print("  MaxiaSalesAgent + qwen3:30b-a3b-instruct-2507 - Phase 0 validation")
    print("=" * 70)
    print()

    try:
        agent = MaxiaSalesAgent()
    except Exception as e:
        print(f"[FAIL] agent init: {e}")
        return 2
    print(f"[OK] Agent initialized (catalog blob len = {len(agent._catalog_blob)})")
    print()

    dialogues = [
        (
            "1. Cold prospect (FR)",
            "test:cold_fr",
            [
                "Bonjour, je construis un agent IA pour faire du trading auto sur Solana, c'est quoi MAXIA ?",
                "Quelles sont les chaines supportees et combien vous prenez de commission ?",
                "OK et comment je m'y mets ?",
            ],
        ),
        (
            "2. Objection prix (FR)",
            "test:price_fr",
            [
                "J'ai vu votre site, 1.5% de commission c'est trop cher pour moi",
                "Vous avez quoi comme tier pour les petits volumes ?",
            ],
        ),
        (
            "3. Question technique MCP (FR)",
            "test:mcp_fr",
            [
                "Comment je connecte mon agent Claude a MAXIA via MCP ?",
                "Et si j'utilise LangChain a la place de Claude ?",
            ],
        ),
    ]

    all_latencies: list[float] = []

    for title, conv_id, user_msgs in dialogues:
        print("-" * 70)
        print(f"  {title}  ({conv_id})")
        print("-" * 70)
        for i, um in enumerate(user_msgs, 1):
            print(f"\n  [USER] {um}")
            t0 = time.time()
            try:
                reply, stage = await agent.reply(conv_id, um)
            except Exception as e:
                reply = f"<ERROR {type(e).__name__}: {e}>"
                stage = None
            dt = time.time() - t0
            all_latencies.append(dt)
            print(f"  [BOT]  [{stage.value if stage else '?'}]")
            print(f"         {reply[:500]}")
            print(f"  [lat]  {dt:.2f}s")
        print()

    if all_latencies:
        p50 = statistics.median(all_latencies)
        sorted_lat = sorted(all_latencies)
        idx95 = max(0, int(len(sorted_lat) * 0.95) - 1)
        p95 = sorted_lat[idx95]
        p_mean = statistics.mean(all_latencies)
        print("=" * 70)
        print(f"  LATENCY  mean={p_mean:.2f}s  p50={p50:.2f}s  p95={p95:.2f}s  n={len(all_latencies)}")
        print("=" * 70)
        if p95 > 8.0:
            print("[FAIL] p95 > 8s — need model tuning")
            return 1
        if p50 > 5.0:
            print("[WARN] p50 > 5s — acceptable but monitor")
        else:
            print("[OK] Latency within target (p50 <= 5s, p95 <= 8s)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
