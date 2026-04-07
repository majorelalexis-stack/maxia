"""Mission 1 — Tweet feature du jour (PROPOSE, ne poste pas).

Generates 1 tweet per day presenting a MAXIA feature (rotation through MAXIA_FEATURES).
The tweet is NOT posted directly — it is sent to Alexis via Telegram for approval.
Alexis copies the text and posts manually.
"""
import logging
from datetime import datetime

from config_local import MAXIA_FEATURES, PROPOSE_DONT_POST
from llm import llm
from agents import CEO_SYSTEM_PROMPT
from scheduler import is_off_day
from notifier import notify_telegram, request_approval

log = logging.getLogger("ceo")


async def mission_tweet_feature(mem: dict, actions: dict) -> None:
    """Propose 1 tweet presentant une feature MAXIA (Alexis poste manuellement)."""
    if actions["counts"]["tweet_feature"] >= 1:
        log.info("Tweet deja propose aujourd'hui — skip")
        return

    if is_off_day():
        return

    # Choisir la feature suivante (rotation)
    idx = mem.get("feature_index", 0) % len(MAXIA_FEATURES)
    feature = MAXIA_FEATURES[idx]
    mem["feature_index"] = idx + 1

    # Generer le tweet via LLM
    tweet_text = await llm(
        f"Write a short tweet (max 250 chars) presenting this feature of MAXIA:\n"
        f"Feature: {feature['name']}\n"
        f"Description: {feature['desc']}\n"
        f"Link: https://{feature['link']}\n\n"
        f"Rules:\n- Professional tone, not salesy\n- Include the link\n- End with: #MAXIA #AI #Web3 #Solana\n- Max 250 characters total",
        system=CEO_SYSTEM_PROMPT,
        max_tokens=100,
    )

    if not tweet_text or len(tweet_text) < 20:
        tweet_text = (
            f"{feature['name']} — {feature['desc']}\n\n"
            f"https://{feature['link']}\n\n#MAXIA #AI #Web3 #Solana"
        )

    # PROPOSE — send to Alexis via Telegram, do NOT post directly
    if PROPOSE_DONT_POST:
        log.info("[TWEET] Mode PROPOSE — envoi a Alexis via Telegram")

        # Send the proposed tweet to Alexis via Telegram
        proposal_msg = (
            f"Tweet propose (feature: {feature['name']}):\n\n"
            f"{tweet_text}\n\n"
            f"--- Copie le texte ci-dessus et poste-le manuellement sur X ---"
        )
        await notify_telegram("Tweet du jour", proposal_msg)

        # Log the proposal
        log.info("Tweet propose: %s", tweet_text[:80])
        mem.setdefault("tweets_posted", []).append({
            "date": datetime.now().isoformat(),
            "feature": feature["name"],
            "text": tweet_text[:200],
            "status": "proposed",  # NOT posted
        })
        actions["counts"]["tweet_feature"] = 1

        # Track for feedback loop (Mission 8)
        mem.setdefault("tweet_engagement", []).append({
            "date": datetime.now().isoformat(),
            "feature_name": feature["name"],
            "feature_desc": feature["desc"][:100],
            "tweet_preview": tweet_text[:140],
            "status": "proposed",
        })
        return

    # Legacy path (PROPOSE_DONT_POST=False) — kept for backward compat but should not be used
    log.warning("[TWEET] PROPOSE_DONT_POST is False — posting directly (NOT RECOMMENDED)")
    try:
        from browser_agent import browser
        await browser.post_tweet(tweet_text)
        log.info("Tweet poste: %s", tweet_text[:80])
        mem.setdefault("tweets_posted", []).append({
            "date": datetime.now().isoformat(),
            "feature": feature["name"],
            "text": tweet_text[:200],
            "status": "posted",
        })
        actions["counts"]["tweet_feature"] = 1

        mem.setdefault("tweet_engagement", []).append({
            "date": datetime.now().isoformat(),
            "feature_name": feature["name"],
            "feature_desc": feature["desc"][:100],
            "tweet_preview": tweet_text[:140],
            "status": "posted",
        })
    except Exception as e:
        log.error("Tweet error: %s", e)
