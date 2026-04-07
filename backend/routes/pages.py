"""MAXIA HTML page routes — extracted from main.py."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"


def _serve(filename: str) -> HTMLResponse:
    """Serve an HTML file from frontend directory."""
    path = FRONTEND_DIR / filename
    if not path.exists():
        return HTMLResponse("Page not found", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
#  HTML PAGE ROUTES
# ═══════════════════════════════════════════════════════════


@router.get("/", response_class=HTMLResponse)
async def serve_landing():
    if (FRONTEND_DIR / "landing.html").exists():
        return _serve("landing.html")
    return _serve("index.html")


@router.get("/landing", response_class=HTMLResponse)
async def serve_landing_alias():
    """Alias /landing -> meme page que /."""
    return _serve("landing.html")


@router.get("/v2")
async def serve_landing_v2():
    """Redirige vers la landing principale."""
    return RedirectResponse(url="/", status_code=301)


@router.get("/register", response_class=HTMLResponse)
async def serve_register():
    return _serve("register.html")


@router.get("/app", response_class=HTMLResponse)
async def serve_app():
    """Interface humaine — Web3 Hub (swap, portfolio, GPU, yields, bridge, stocks, NFT)."""
    return _serve("app.html")


@router.get("/status", response_class=HTMLResponse)
async def serve_status():
    """Live status page — all systems, chains, oracles."""
    return _serve("status.html")


@router.get("/docs", response_class=HTMLResponse)
async def serve_docs():
    """API documentation page."""
    return _serve("docs.html")


@router.get("/trust", response_class=HTMLResponse)
async def serve_trust():
    """Trust & Safety page — escrow, OFAC, disputes, SLA."""
    return _serve("trust.html")


@router.get("/compare", response_class=HTMLResponse)
async def serve_compare():
    """Compare MAXIA fees vs competitors — live data."""
    return _serve("compare.html")


@router.get("/store", response_class=HTMLResponse)
async def serve_store():
    """AI Agent App Store — discover and install AI agents."""
    return _serve("store.html")


@router.get("/architecture", response_class=HTMLResponse)
async def serve_architecture():
    """Technical architecture page — system diagrams, failover, security."""
    return _serve("architecture.html")


@router.get("/whitelabel", response_class=HTMLResponse)
async def serve_whitelabel():
    """White-label partner page — use MAXIA infrastructure under your brand."""
    return _serve("whitelabel.html")


@router.get("/enterprise", response_class=HTMLResponse)
async def serve_enterprise():
    """Enterprise page — infrastructure for AI agent companies."""
    return _serve("enterprise.html")


@router.get("/forum", response_class=HTMLResponse)
async def serve_forum():
    """AI Forum — where agents discuss, trade, post bounties, and discover services."""
    return _serve("forum.html")


@router.get("/marketplace", response_class=HTMLResponse)
async def serve_marketplace():
    """Creator Marketplace — buy and sell tools, datasets, prompts, workflows, models."""
    return _serve("marketplace.html")


@router.get("/creator", response_class=HTMLResponse)
async def serve_creator():
    """Creator Dashboard — manage tool listings and track revenue."""
    return _serve("creator.html")


@router.get("/legal", response_class=HTMLResponse)
async def serve_legal():
    """Legal Disclaimer — platform status, tokenized stocks, jurisdictional restrictions."""
    return _serve("legal.html")


@router.get("/profile", response_class=HTMLResponse)
async def serve_profile():
    """Agent Profile — public profile page with stats, badges, and activity."""
    return _serve("profile.html")


@router.get("/referral", response_class=HTMLResponse)
async def serve_referral():
    """Referral program — earn 10% commission on referred agents."""
    return _serve("referral.html")


@router.get("/launch", response_class=HTMLResponse)
async def serve_launch():
    """Product Hunt launch page (S41)."""
    return _serve("launch.html")


@router.get("/blog", response_class=HTMLResponse)
async def serve_blog():
    """Blog — knowledge base with market analysis, tutorials, and announcements."""
    return _serve("blog.html")


@router.get("/governance", response_class=HTMLResponse)
async def serve_governance():
    """Governance — vote on platform decisions and feature requests."""
    return _serve("governance.html")


@router.get("/playground", response_class=HTMLResponse)
async def serve_playground():
    """API Playground — test endpoints in the browser with zero setup."""
    return _serve("playground.html")


@router.get("/buy", response_class=HTMLResponse)
async def serve_buy():
    """Buy crypto with credit card — fiat on-ramp page."""
    return _serve("buy.html")


@router.get("/changelog", response_class=HTMLResponse)
async def serve_changelog():
    """Changelog — latest updates, features, and fixes."""
    return _serve("changelog.html")


@router.get("/sniper", response_class=HTMLResponse)
async def serve_sniper():
    """Token sniper — real-time new token detection from pump.fun."""
    return _serve("sniper.html")


@router.get("/about", response_class=HTMLResponse)
async def serve_about():
    """About MAXIA — platform overview, infrastructure, contact."""
    return _serve("about.html")


@router.get("/faq", response_class=HTMLResponse)
async def serve_faq():
    """FAQ — frequently asked questions about MAXIA."""
    return _serve("faq.html")


@router.get("/miniapp", response_class=HTMLResponse)
async def serve_miniapp():
    """Telegram Mini App — trading UI inside Telegram."""
    return _serve("miniapp.html")


@router.get("/pitch", response_class=HTMLResponse)
async def serve_pitch():
    """Investor pitch deck — 10 slides HTML."""
    return _serve("pitch.html")


@router.get("/exec-summary", response_class=HTMLResponse)
async def serve_exec_summary():
    """Executive summary — 1 page investor overview."""
    return _serve("exec-summary.html")


@router.get("/metrics-public", response_class=HTMLResponse)
async def serve_metrics_public():
    """Public metrics dashboard — live platform stats."""
    return _serve("metrics.html")


@router.get("/pricing", response_class=HTMLResponse)
async def serve_pricing():
    """Pricing page — all fee tiers (swap, marketplace, GPU, API)."""
    return _serve("pricing.html")


@router.get("/feedback", response_class=HTMLResponse)
async def serve_feedback():
    """Feedback and bug report form."""
    return _serve("feedback.html")
