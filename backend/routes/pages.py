"""MAXIA HTML page routes — extracted from main.py."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"


def _serve(filename: str) -> HTMLResponse:
    """Serve an HTML file from frontend directory."""
    path = FRONTEND_DIR / filename
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    return HTMLResponse("Page not found", status_code=404)


# ═══════════════════════════════════════════════════════════
#  HTML PAGE ROUTES
# ═══════════════════════════════════════════════════════════


@router.get("/", response_class=HTMLResponse)
async def serve_landing():
    path = FRONTEND_DIR / "landing.html"
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA</h1><p>Page introuvable.</p>")


@router.get("/landing", response_class=HTMLResponse)
async def serve_landing_alias():
    """Alias /landing -> meme page que /."""
    path = FRONTEND_DIR / "landing.html"
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA</h1>")


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
