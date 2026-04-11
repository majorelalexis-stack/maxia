"""Mission 3 — Daily report: GitHub releases + agent scan + directories.

Sends a daily email at 9h with GitHub intelligence, directory suggestions, and new agents.
"""
import asyncio
import logging
from datetime import datetime

import httpx

from config_local import VPS_URL, GITHUB_REPOS
from llm import llm
from agents import CEO_SYSTEM_PROMPT
from scheduler import send_mail

log = logging.getLogger("ceo")


async def mission_daily_report(mem: dict, actions: dict) -> None:
    """Scan GitHub repos, cherche skills/annuaires, envoie rapport."""
    if actions["counts"]["report_sent"] >= 1:
        log.info("Rapport deja envoye aujourd'hui — skip")
        return

    report_parts = []

    # 3a: GitHub repos
    report_parts.append("=== GITHUB REPOS — NOUVEAUTES ===\n")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for repo in GITHUB_REPOS[:10]:  # Max 10 pour pas rate limit
                try:
                    resp = await client.get(
                        f"https://api.github.com/repos/{repo}/releases/latest",
                        headers={"Accept": "application/vnd.github.v3+json"},
                    )
                    if resp.status_code == 200:
                        release = resp.json()
                        pub_date = release.get("published_at", "")[:10]
                        # Seulement les releases des 7 derniers jours
                        if pub_date >= (datetime.now().strftime("%Y-%m-") + "01"):
                            report_parts.append(
                                f"- {repo}: {release.get('name', 'new release')} ({pub_date})\n"
                            )
                            report_parts.append(f"  {release.get('body', '')[:200]}\n\n")
                except Exception:
                    pass
                await asyncio.sleep(1)  # Rate limit GitHub API
    except Exception as e:
        report_parts.append(f"  Erreur scan GitHub: {e}\n")

    # 3a-bis: LLM analysis of GitHub findings
    github_summary = "".join(report_parts)
    if "new release" in github_summary.lower() or len(report_parts) > 2:
        llm_analysis = await llm(
            f"Here are recent GitHub releases from projects in the AI agent ecosystem:\n\n"
            f"{github_summary}\n\n"
            f"MAXIA is an AI-to-AI marketplace on 14 blockchains (Solana, Base, etc.) with on-chain escrow, "
            f"token swaps, GPU rental, and 17 AI services.\n\n"
            f"For each release, explain in 1-2 sentences:\n"
            f"1. What this means for MAXIA (opportunity, threat, or neutral)\n"
            f"2. Any concrete action MAXIA should take\n\n"
            f"Prioritize findings by relevance to MAXIA (most relevant first).",
            system="You are the MAXIA CEO analyzing competitive intelligence. Be concise and actionable.",
            max_tokens=600,
        )
        if llm_analysis:
            report_parts.append("\n=== ANALYSE CEO — Impact pour MAXIA ===\n")
            report_parts.append(llm_analysis + "\n")

    # 3b: Annuaires ou inscrire MAXIA
    report_parts.append("\n=== ANNUAIRES & VISIBILITE ===\n")
    directories = await llm(
        "List 5 websites or directories where an AI-to-AI marketplace should be listed for visibility. "
        "For each, give: name, URL, and what needs to be done to register. "
        "Focus on: AI directories, Product Hunt alternatives, agent registries, crypto/DeFi directories. "
        "Format: 1. Name — URL — Action needed",
        system="You are an AI marketing expert. Give practical, actionable suggestions.",
        max_tokens=500,
    )
    report_parts.append(directories + "\n" if directories else "  Pas de suggestions\n")

    # 3c: Nouveaux agents inscrits
    report_parts.append("\n=== NOUVEAUX AGENTS INSCRITS ===\n")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{VPS_URL}/api/public/leaderboard")
            if resp.status_code == 200:
                data = resp.json()
                agents = data.get("agents", data.get("leaderboard", []))
                known = set(mem.get("agents_seen", []))
                new_agents = [a for a in agents if a.get("name", "") not in known]
                if new_agents:
                    for a in new_agents[:10]:
                        report_parts.append(
                            f"  - {a.get('name', '?')} (wallet: {str(a.get('wallet', ''))[:12]}...)\n"
                        )
                        mem["agents_seen"].append(a.get("name", ""))
                else:
                    report_parts.append("  Aucun nouvel agent\n")
    except Exception as e:
        report_parts.append(f"  Erreur: {e}\n")

    # Envoyer le mail
    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — Rapport quotidien du {today}\n\n" + "".join(report_parts)
    await send_mail(f"[MAXIA CEO] Rapport quotidien - {today}", body)
    actions["counts"]["report_sent"] = 1
