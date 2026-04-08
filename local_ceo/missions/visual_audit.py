"""Mission — Visual Audit: screenshot all MAXIA pages and analyze with Qwen Vision.

Takes a screenshot of every page, sends each to qwen2.5vl (vision model)
for visual analysis, then sends a full report by email + Telegram.
"""
import asyncio
import base64
import logging
import os
import time

import httpx

from config_local import OLLAMA_URL, OLLAMA_VISION_MODEL, VPS_URL
from scheduler import send_mail

log = logging.getLogger("ceo")

# All pages to audit
PAGES = [
    ("/", "Landing"),
    ("/pricing", "Pricing"),
    ("/agents", "Agent Economy"),
    ("/agent-dashboard", "Agent Dashboard"),
    ("/blog", "Blog"),
    ("/store", "App Store"),
    ("/marketplace", "Marketplace"),
    ("/docs", "API Docs"),
    ("/enterprise", "Enterprise"),
    ("/about", "About"),
    ("/metrics-public", "Metrics"),
    ("/compare", "Compare"),
    ("/buy", "Buy Crypto"),
    ("/sniper", "Token Sniper"),
    ("/forum", "Forum"),
    ("/referral", "Referral"),
    ("/register", "Register"),
    ("/legal", "Legal"),
    ("/terms", "Terms"),
    ("/privacy", "Privacy"),
    ("/trust", "Trust & Safety"),
    ("/faq", "FAQ"),
    ("/changelog", "Changelog"),
    ("/status", "Status"),
    ("/feedback", "Feedback"),
    ("/governance", "Governance"),
    ("/pitch", "Pitch Deck"),
    ("/whitelabel", "White Label"),
    ("/architecture", "Architecture"),
    ("/profile", "Profile"),
]

AUDIT_PROMPT = """You are a senior UI/UX auditor. Analyze this screenshot of a web page.

Report ONLY problems — do NOT say what looks good. Be specific and brutal.

Check for:
1. NAV BAR: Is it the same as other pages? Are links spaced properly? Is "Launch App" button visible?
2. LAYOUT: Is content contained (max-width ~1100px centered) or does it bleed full-width?
3. BACKGROUND: Is there the dark animated background (#060a14 with cyan/purple blobs)?
4. FOOTER: Is there a 4-column footer (Trade, Explore, Community, Legal)?
5. TYPOGRAPHY: Are fonts consistent (Syne for headings, DM Sans for body)?
6. PROPORTIONS: Are cards/sections too big, too small, or properly sized?
7. SPACING: Is there proper padding between sections? Is content hidden behind the nav bar?
8. MOBILE: Does it look like it would break on mobile?

Page: {page_name} ({page_url})

Format your response as:
PROBLEMS:
- [SEVERITY:HIGH/MEDIUM/LOW] Description of the problem

If the page looks perfect, say: NO ISSUES FOUND"""


async def _screenshot_page(url: str) -> str:
    """Take a screenshot using Playwright and return the file path."""
    try:
        from browser_agent import browser
        path = await browser.screenshot_page(url)
        return path
    except Exception as e:
        log.error("[VisualAudit] Screenshot error for %s: %s", url, e)
        return ""


async def _analyze_screenshot(image_path: str, page_name: str, page_url: str) -> str:
    """Send screenshot to Qwen Vision for analysis."""
    if not image_path or not os.path.exists(image_path):
        return f"SCREENSHOT FAILED for {page_name}"

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = AUDIT_PROMPT.format(page_name=page_name, page_url=page_url)

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": OLLAMA_VISION_MODEL,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "options": {"num_predict": 500, "temperature": 0.3},
            })
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception as e:
        log.error("[VisualAudit] Vision analysis error for %s: %s", page_name, e)
        return f"ANALYSIS FAILED for {page_name}: {e}"


async def mission_visual_audit(mem: dict, actions: dict) -> None:
    """Screenshot all MAXIA pages and analyze with vision model.

    Sends a full audit report by email with all issues found.
    """
    if actions["counts"].get("visual_audit", 0) >= 1:
        return

    log.info("═══ VISUAL AUDIT — Scanning %d pages ═══", len(PAGES))

    results = []
    issues_count = 0

    for path, name in PAGES:
        url = f"{VPS_URL}{path}"
        log.info("[VisualAudit] Scanning %s (%s)...", name, path)

        # Screenshot
        img_path = await _screenshot_page(url)
        if not img_path:
            results.append(f"\n{'='*50}\n{name} ({path})\n{'='*50}\nSCREENSHOT FAILED\n")
            continue

        # Analyze
        analysis = await _analyze_screenshot(img_path, name, path)
        has_issues = "NO ISSUES FOUND" not in analysis
        if has_issues:
            issues_count += 1

        results.append(f"\n{'='*50}\n{name} ({path})\n{'='*50}\n{analysis}\n")

        # Cleanup screenshot
        try:
            os.remove(img_path)
        except Exception:
            pass

        # Small delay to avoid GPU overload
        await asyncio.sleep(2)

    # Build report
    today = time.strftime("%d/%m/%Y %H:%M")
    report = f"MAXIA Visual Audit — {today}\n"
    report += f"Pages scanned: {len(PAGES)}\n"
    report += f"Pages with issues: {issues_count}\n"
    report += f"Pages OK: {len(PAGES) - issues_count}\n"
    report += "\n".join(results)

    report += f"\n\n--- END OF AUDIT ---\n"
    report += f"Generated by CEO Visual Audit (Qwen Vision)\n"

    # Send by email
    await send_mail(
        f"[MAXIA CEO] Visual Audit — {issues_count} pages with issues",
        report,
    )

    actions["counts"]["visual_audit"] = 1
    log.info("[VisualAudit] Done. %d/%d pages have issues. Report sent by email.",
             issues_count, len(PAGES))
