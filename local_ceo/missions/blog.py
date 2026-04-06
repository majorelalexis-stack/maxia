"""Mission 21 — Blog: generate and publish 1 article/day via Ollama.

Fetches RSS feeds, generates a Web3 article via Ollama (Qwen 3 14B),
and publishes it on the VPS blog. Runs once per day at 8h UTC.
"""
import logging

log = logging.getLogger("ceo")


async def mission_blog_post(mem: dict, actions: dict) -> None:
    """Generate and publish a daily blog article via Ollama + VPS API."""
    if actions["counts"].get("blog_posted", 0) >= 1:
        log.info("[BLOG] Article deja publie aujourd'hui — skip")
        return

    try:
        from blog_writer import run_daily_blog
    except ImportError as e:
        log.error("[BLOG] Cannot import blog_writer: %s", e)
        return

    log.info("[BLOG] Lancement generation article quotidien...")

    try:
        result = await run_daily_blog()
    except Exception as e:
        log.error("[BLOG] run_daily_blog error: %s", str(e)[:200])
        return

    if result:
        slug = result.get("slug", "")
        log.info("[BLOG] Article publie avec succes (slug: %s)", slug)
        actions["counts"]["blog_posted"] = 1
    else:
        log.info("[BLOG] Pas d'article publie (deja poste ou erreur Ollama/VPS)")
