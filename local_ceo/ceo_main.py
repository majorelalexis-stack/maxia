"""CEO Local V3 — Entry point. Refactored from ceo_local_v2.py.

18 missions, dual-model (Qwen 3.5 27B + VL 7B), zero spam.
Usage:
  python ceo_main.py          # autonomous mode (all missions)
  python ceo_main.py chat     # interactive terminal mode
"""
import asyncio
import logging
import os
import sys
import time
from datetime import datetime

from config_local import (
    OLLAMA_MODEL, ALEXIS_EMAIL, VPS_URL,
    HEALTH_CHECK_INTERVAL_S, MODERATION_INTERVAL_S,
)
from llm import llm, last_llm_call
import llm as llm_module
from memory import (
    load_memory, save_memory, load_actions_today, save_actions,
    init_db, migrate_json_to_sqlite, log_action, cleanup_old_data,
)
from agents import CEO_SYSTEM_PROMPT
from scheduler import run_mission

# Mission imports
from missions.health import mission_health_check, mission_health_report
from missions.tweet import mission_tweet_feature
from missions.opportunities import (
    mission_twitter_scan_hourly, mission_github_scan_hourly,
    mission_reddit_scan_hourly,
    mission_send_best_opportunities,
)
from missions.report import mission_daily_report
from missions.moderation import mission_moderate_forum
from missions.competitive import mission_competitive_watch
from missions.code_audit import mission_code_audit
from missions.scout import mission_scout_scan, mission_scout_execute_approved
from missions.email_check import mission_check_alexis_emails, mission_changelog_forum
from missions.email_outreach import mission_email_outreach
from missions.strategy import mission_strategy_review
from missions.telegram_chat import mission_telegram_chat
from missions.blog import mission_blog_post

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CEO] %(message)s")
log = logging.getLogger("ceo")

# Mining Kaspa — DESACTIVE (non rentable mars 2026, ASICs ont tue le GPU mining)
KASPA_MINING_ENABLED = os.getenv("KASPA_MINING_ENABLED", "0") == "1"


# ══════════════════════════════════════════
# Boucle principale
# ══════════════════════════════════════════

async def run():
    """Boucle principale du CEO Local V3."""
    log.info("═══════════════════════════════════════")
    log.info("  MAXIA CEO Local V3 — demarrage")
    log.info("  Modele: %s", OLLAMA_MODEL)
    log.info("  Email: %s", ALEXIS_EMAIL)
    log.info("  VPS: %s", VPS_URL)
    log.info("  Kaspa Mining: %s", "ACTIF" if KASPA_MINING_ENABLED else "DESACTIVE")
    log.info("═══════════════════════════════════════")

    # Initialize SQLite memory and migrate JSON data (one-time)
    init_db()
    migrated = migrate_json_to_sqlite()
    if any(v > 0 for v in migrated.values()):
        log.info("JSON→SQLite migration: %s", migrated)

    # Cleanup old data on startup (>90 days)
    cleanup_old_data(90)

    # Demarrer le miner Kaspa si active
    if KASPA_MINING_ENABLED:
        try:
            from kaspa_miner import start_miner
            if start_miner():
                log.info("[MINING] Kaspa miner demarre au boot")
            else:
                log.warning("[MINING] Echec demarrage miner — verifier TeamRedMiner")
        except ImportError:
            log.warning("[MINING] kaspa_miner module not found")

    mem = load_memory()
    mem.setdefault("todays_opportunities", [])
    last_health = 0
    last_mining_stats = 0
    last_moderation = 0
    last_twitter_scan = 0
    last_tweet = 0
    last_opportunities_mail = 0
    last_report = 0
    last_competitive = 0

    while True:
        try:
            now = time.time()
            dt_now = datetime.now()
            hour = dt_now.hour
            weekday = dt_now.weekday()  # 0=lundi, 6=dimanche
            actions = load_actions_today()

            # Mission 7: Health check (toutes les 5 min)
            if now - last_health >= HEALTH_CHECK_INTERVAL_S:
                await mission_health_check(mem)
                last_health = now
                actions["counts"]["health_checks"] = actions["counts"].get("health_checks", 0) + 1

            # Mission 4: Moderation forum (toutes les heures)
            if now - last_moderation >= MODERATION_INTERVAL_S:
                await mission_moderate_forum(mem)
                last_moderation = now
                actions["counts"]["moderation_done"] = actions["counts"].get("moderation_done", 0) + 1

            # Mission 2a: Scan ALL platforms a 18h30 (concentre, 1x/jour)
            if hour == 18 and dt_now.minute >= 30 and actions["counts"].get("scan_done", 0) == 0:
                if now - last_twitter_scan >= 3600:
                    log.info("═══ SCAN OPPORTUNITES 18h30 ═══")
                    await mission_twitter_scan_hourly(mem)
                    await mission_github_scan_hourly(mem)
                    await mission_reddit_scan_hourly(mem)
                    last_twitter_scan = now
                    actions["counts"]["scan_done"] = 1

            # Mission 2b: Envoyer le best-of score par mail (19h30+)
            if hour >= 19 and (hour > 19 or dt_now.minute >= 30) and actions["counts"].get("opportunities_sent", 0) == 0:
                if now - last_opportunities_mail >= 3600:
                    await run_mission("opportunities_mail", mission_send_best_opportunities(mem, actions), mem, actions)
                    last_opportunities_mail = now

            # Mission 3: Rapport quotidien GitHub+agents+annuaires (9h, mail separe)
            if hour == 9 and actions["counts"].get("report_sent", 0) == 0:
                if now - last_report >= 3600:
                    await run_mission("daily_report", mission_daily_report(mem, actions), mem, actions)
                    last_report = now

            # Mission 16: Health report intelligent (8h, mail separe)
            if hour == 8 and actions["counts"].get("health_report_sent", 0) == 0:
                await run_mission("health_report", mission_health_report(mem, actions), mem, actions)

            # Mission 21: Blog article quotidien (8h, 1x/jour)
            if hour == 8 and actions["counts"].get("blog_posted", 0) == 0:
                await run_mission("blog_post", mission_blog_post(mem, actions), mem, actions)

            # Mission 13: Changelog forum (dimanche 11h)
            if weekday == 6 and hour == 11 and actions["counts"].get("changelog_posted", 0) == 0:
                await run_mission("changelog", mission_changelog_forum(mem, actions), mem, actions)

            # Mission 1: Tweet feature (14h-17h — elargi pour ne pas rater la fenetre)
            if 14 <= hour <= 17 and actions["counts"].get("tweet_feature", 0) == 0:
                if now - last_tweet >= 3600:
                    log.info("[TWEET] Fenetre tweet active (hour=%d) — lancement mission_tweet_feature", hour)
                    await run_mission("tweet", mission_tweet_feature(mem, actions), mem, actions)
                    last_tweet = now

            # Mission 6: Veille concurrentielle + memo strategique (19h, 1x/jour)
            if hour == 19 and actions["counts"].get("competitive_watch", 0) == 0:
                if now - last_competitive >= 3600:
                    await run_mission("competitive_watch", mission_competitive_watch(mem, actions), mem, actions)
                    last_competitive = now

            # Mission 17: Scout AI — scan registries (17h, 1x/jour)
            if hour == 17 and actions["counts"].get("scout_done", 0) == 0:
                await run_mission("scout_scan", mission_scout_scan(mem, actions), mem, actions)

            # Mission 17b: Scout — execute les contacts approuves par Alexis (toutes les 5 min)
            if now - mem.get("_last_scout_check", 0) >= 300:
                await run_mission("scout_execute", mission_scout_execute_approved(mem), mem, actions)
                mem["_last_scout_check"] = now

            # Mission 9: Check emails de Alexis (toutes les 5 min)
            if now - mem.get("_last_email_check", 0) >= 300:
                await mission_check_alexis_emails(mem)
                mem["_last_email_check"] = now

            # Mission 18: Email Outreach — cold emails to approved contacts (10h, 1x/jour)
            if hour == 10 and actions["counts"].get("outreach_sent", 0) == 0:
                await run_mission("email_outreach", mission_email_outreach(mem, actions), mem, actions)

            # Mission 19: Strategy Review — weekly (dimanche 20h)
            if weekday == 6 and hour == 20 and actions["counts"].get("strategy_review", 0) == 0:
                await run_mission("strategy_review", mission_strategy_review(mem, actions), mem, actions)

            # Mission 20: Telegram Chat — poll messages from Alexis (toutes les 2 min)
            if now - mem.get("_last_telegram_poll", 0) >= 120:
                await run_mission("telegram_chat", mission_telegram_chat(mem, actions), mem, actions)
                mem["_last_telegram_poll"] = now

            # Mining — relancer si GPU libre depuis 60s (pas d'appel LLM recent)
            if KASPA_MINING_ENABLED:
                try:
                    from kaspa_miner import is_mining, start_miner, get_stats
                    if not is_mining() and llm_module.last_llm_call > 0:
                        idle_since = now - llm_module.last_llm_call
                        if idle_since >= 60:
                            start_miner()
                            log.info("[MINING] Miner relance (GPU idle depuis %ds)", int(idle_since))

                    # Mining stats (toutes les heures)
                    if now - last_mining_stats >= 3600:
                        stats = get_stats()
                        log.info("[MINING] Stats: mining=%s, total=%.2fh, starts=%d, stops=%d, hashrate=%s",
                                 stats["is_mining"], stats["total_mining_hours"],
                                 stats["starts"], stats["stops"], stats["hashrate"])
                        last_mining_stats = now
                except ImportError:
                    pass

            # Mission 10: Code Audit — EN DERNIER (bloquant, ne doit pas empecher les autres missions)
            audit_done_today = actions["counts"].get("audit_complete", 0) >= 1
            if not audit_done_today and hour >= 19 and dt_now.minute >= 15:
                is_complete = await mission_code_audit(mem, actions)
                if is_complete:
                    actions["counts"]["audit_complete"] = 1
                    log.info("[AUDIT] Audit quotidien termine — mail envoye")

            # Sauvegarder
            save_memory(mem)
            save_actions(actions)

        except Exception as e:
            log.error("Boucle principale error: %s", e)

        await asyncio.sleep(60)  # Check toutes les minutes


# ══════════════════════════════════════════
# Mode terminal interactif
# ══════════════════════════════════════════

async def terminal_mode():
    """Mode interactif — parler au CEO en direct."""
    print("\n  +---------------------------------------+")
    print("  |  MAXIA CEO — Mode Terminal            |")
    print("  |  Tape ta question, 'quit' pour sortir |")
    print("  +---------------------------------------+\n")

    mem = load_memory()

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n[Alexis] > ")
            )
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input.strip():
            continue
        if user_input.strip().lower() in ("quit", "exit", "q"):
            print("[CEO] Au revoir, Alexis.")
            break

        cmd = user_input.strip().lower()

        # Commandes speciales
        if cmd == "status":
            print(f"[CEO] Tweets postes: {len(mem.get('tweets_posted', []))}")
            print(f"[CEO] Opportunites accumulees: {len(mem.get('todays_opportunities', []))}")
            print(f"[CEO] Agents vus: {len(mem.get('agents_seen', []))}")
            print(f"[CEO] Health alerts: {len(mem.get('health_alerts', []))}")
            continue

        if cmd == "scan twitter":
            print("[CEO] Scan Twitter en cours...")
            await mission_twitter_scan_hourly(mem)
            save_memory(mem)
            print(f"[CEO] Done — {len(mem.get('todays_opportunities', []))} opportunites Twitter accumulees")
            continue

        if cmd == "scan github":
            print("[CEO] Scan GitHub issues/discussions en cours...")
            await mission_github_scan_hourly(mem)
            save_memory(mem)
            print(f"[CEO] Done — {len(mem.get('todays_github_opportunities', []))} opportunites GitHub accumulees")
            continue

        if cmd == "rapport":
            print("[CEO] Generation du rapport quotidien...")
            actions = load_actions_today()
            await mission_daily_report(mem, actions)
            save_memory(mem)
            save_actions(actions)
            print("[CEO] Rapport envoye par mail.")
            continue

        if cmd == "health":
            print("[CEO] Health check...")
            await mission_health_check(mem)
            save_memory(mem)
            continue

        if cmd in ("audit", "code audit"):
            print("[CEO] Code audit en cours — scan de TOUS les fichiers backend...")
            actions = load_actions_today()
            file_count = 0
            while True:
                is_done = await mission_code_audit(mem, actions)
                file_count += 1
                if is_done:
                    break
                if file_count % 10 == 0:
                    print(f"[CEO] ... {file_count} fichiers scannes")
            save_memory(mem)
            save_actions(actions)
            print("[CEO] Audit complet!")
            continue

        if cmd == "blog":
            print("[CEO] Generation article blog...")
            actions = load_actions_today()
            await mission_blog_post(mem, actions)
            save_memory(mem)
            save_actions(actions)
            continue

        if cmd == "tweet":
            print("[CEO] Redaction du tweet...")
            actions = load_actions_today()
            await mission_tweet_feature(mem, actions)
            save_memory(mem)
            save_actions(actions)
            continue

        if cmd == "send opportunities":
            print("[CEO] Envoi du best-of Twitter...")
            actions = load_actions_today()
            await mission_send_best_opportunities(mem, actions)
            save_memory(mem)
            save_actions(actions)
            continue

        if cmd == "help":
            print("[CEO] Commandes disponibles:")
            print("  audit            — lancer l'audit code complet")
            print("  blog             — generer et publier un article blog")
            print("  status           — voir les stats du jour")
            print("  scan twitter     — forcer un scan Twitter maintenant")
            print("  scan github      — forcer un scan GitHub + rapport")
            print("  health           — verifier la sante du site")
            print("  tweet            — poster le tweet du jour")
            print("  send opportunities — envoyer le best-of Twitter par mail")
            print("  help             — cette aide")
            print("  quit             — quitter")
            print("  (ou pose une question libre)")
            continue

        # Question libre -> LLM
        print("[CEO] Reflexion...")
        response = await llm(
            f"Alexis asks: {user_input}\n\nRespond as the MAXIA CEO. Be helpful and factual. Use your knowledge of MAXIA.",
            system=CEO_SYSTEM_PROMPT,
            max_tokens=500,
        )
        if response:
            # Nettoyer le thinking de Qwen3
            if "<think>" in response and "</think>" in response:
                response = response.split("</think>")[-1].strip()
            print(f"\n[CEO] {response}")
        else:
            print("[CEO] Erreur LLM — pas de reponse")


def main():
    """Entry point for CEO Local."""
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        print("""
    +---------------------------------------+
    |    MAXIA CEO Local V3 — Chat Mode     |
    +---------------------------------------+
        """)
        asyncio.run(terminal_mode())
    else:
        print("""
    +---------------------------------------+
    |    MAXIA CEO Local V3                 |
    |    18 missions - 1 modele - 0 spam    |
    +---------------------------------------+
        """)
        asyncio.run(run())


if __name__ == "__main__":
    main()
