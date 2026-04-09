"""MAXIA — Resolve Discord invite codes to guild/channel IDs.

Uses the PUBLIC Discord API endpoint:
    GET /api/v10/invites/{code}?with_counts=true

No authentication required. Returns guild info, channel info, and
approximate member/presence counts so we can verify a server is active
before recommending it to Alexis.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Optional

import httpx

# Force UTF-8 on Windows stdout so we can print Japanese/Arabic guild names
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

DISCORD_API = "https://discord.com/api/v10"

# Candidate invite codes found via web search (2026-04-09)
CANDIDATES: list[dict] = [
    {
        "code": "solana",
        "region": "global",
        "lang": "en",
        "why": "Official Solana Tech — devs + validators + core engineering",
    },
    {
        "code": "stjp",
        "region": "japan",
        "lang": "ja/en",
        "why": "Superteam Japan — Solana ecosystem hub JP",
    },
    {
        "code": "Mq3ReaekgG",
        "region": "india",
        "lang": "en",
        "why": "Superteam India — Solana talent + bounties (NOTE: India geo-blocked)",
    },
    {
        "code": "pHkTA9QJcm",
        "region": "latam",
        "lang": "pt-br",
        "why": "Fraternidade Crypto Brasil — 16k+ members, Web3 since 2019",
    },
    {
        "code": "cryptocom",
        "region": "global",
        "lang": "en",
        "why": "Crypto.com — massive community (use as high-volume discovery)",
    },
    # Extra candidates worth testing
    {
        "code": "ethereum",
        "region": "global",
        "lang": "en",
        "why": "Official Ethereum Discord (fallback if 'ethereum' invite active)",
    },
    {
        "code": "base",
        "region": "global",
        "lang": "en",
        "why": "Base L2 by Coinbase",
    },
]


@dataclass(frozen=True)
class InviteInfo:
    code: str
    valid: bool
    guild_id: str = ""
    guild_name: str = ""
    channel_id: str = ""
    channel_name: str = ""
    members_total: int = 0
    members_online: int = 0
    verified: bool = False
    partnered: bool = False
    error: str = ""
    meta: dict = field(default_factory=dict)


async def resolve(client: httpx.AsyncClient, code: str) -> InviteInfo:
    url = f"{DISCORD_API}/invites/{code}?with_counts=true&with_expiration=true"
    try:
        resp = await client.get(url, timeout=10.0)
    except httpx.HTTPError as e:
        return InviteInfo(code=code, valid=False, error=f"http error: {e}")

    if resp.status_code == 404:
        return InviteInfo(code=code, valid=False, error="404 invite not found or expired")
    if resp.status_code != 200:
        return InviteInfo(
            code=code, valid=False,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )

    try:
        data = resp.json()
    except Exception as e:
        return InviteInfo(code=code, valid=False, error=f"json error: {e}")

    guild = data.get("guild", {}) or {}
    channel = data.get("channel", {}) or {}
    features = set(guild.get("features", []) or [])

    return InviteInfo(
        code=code,
        valid=True,
        guild_id=str(guild.get("id", "")),
        guild_name=str(guild.get("name", "")),
        channel_id=str(channel.get("id", "")),
        channel_name=str(channel.get("name", "")),
        members_total=int(data.get("approximate_member_count", 0) or 0),
        members_online=int(data.get("approximate_presence_count", 0) or 0),
        verified="VERIFIED" in features,
        partnered="PARTNERED" in features,
    )


async def main() -> None:
    results: list[tuple[dict, InviteInfo]] = []
    async with httpx.AsyncClient() as client:
        for candidate in CANDIDATES:
            info = await resolve(client, candidate["code"])
            results.append((candidate, info))
            await asyncio.sleep(0.3)  # be gentle with Discord

    print("\n" + "=" * 78)
    print(" MAXIA — Discord invite resolver (public API)")
    print("=" * 78 + "\n")

    output: list[dict] = []
    for candidate, info in results:
        marker = "+" if info.valid else "-"
        print(f"[{marker}] discord.gg/{info.code}")
        if info.valid:
            flags = []
            if info.verified:
                flags.append("VERIFIED")
            if info.partnered:
                flags.append("PARTNERED")
            flag_str = " ".join(flags) or "-"
            print(f"    name:     {info.guild_name}")
            print(f"    guild_id: {info.guild_id}")
            print(f"    channel:  #{info.channel_name} ({info.channel_id})")
            print(f"    members:  {info.members_total:,} ({info.members_online:,} online)")
            print(f"    flags:    {flag_str}")
            print(f"    region:   {candidate['region']}")
            print(f"    why:      {candidate['why']}")
            output.append({
                "code": info.code,
                "invite_url": f"https://discord.gg/{info.code}",
                "guild_id": info.guild_id,
                "guild_name": info.guild_name,
                "channel_id": info.channel_id,
                "channel_name": info.channel_name,
                "members_total": info.members_total,
                "members_online": info.members_online,
                "verified": info.verified,
                "partnered": info.partnered,
                "region": candidate["region"],
                "lang": candidate["lang"],
                "why": candidate["why"],
            })
        else:
            print(f"    ERROR: {info.error}")
            print(f"    region: {candidate['region']} (not added to shortlist)")
        print()

    # Save valid ones
    valid_count = len(output)
    print(f"\n{valid_count}/{len(CANDIDATES)} invites valid.")
    if output:
        # Sort by members_online descending (most active first)
        output.sort(key=lambda x: x["members_online"], reverse=True)
        import os
        out_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "local_ceo", "memory_prod",
            "discord_candidates.json",
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "resolved_at": 0,
                "candidates": output,
            }, f, ensure_ascii=False, indent=2)
        print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
