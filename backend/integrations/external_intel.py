"""MAXIA External Intelligence — 8 free API integrations.

All endpoints use free public APIs (zero cost, zero Apify dependency).
Each service has rate limiting, caching, and graceful fallback.

Services:
  I1: SEC EDGAR Insider Trading (free, 10 req/s)
  I2: NVD CVE Security Feed (free, 50 req/30s)
  I3: GitHub Trending Repos (free, 5000 req/h)
  I4: Tavily Deep Research (1000 free/month, needs TAVILY_API_KEY)
  I5: Helius Whale Enhanced (free tier, needs HELIUS_API_KEY)
  I6: OpenCorporates Company Lookup (free, limited)
  I7: Competitive Scanner (uses existing web_scraper)
  I8: SEO Domain Checker (free APIs)
"""
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from core.http_client import get_http_client
from core.error_utils import safe_error
from core.security import check_rate_limit

logger = logging.getLogger("maxia.intel")


async def _rate_limit_intel(request: Request) -> None:
    """H16 fix: rate limit intel endpoints to prevent API quota exhaustion."""
    await check_rate_limit(request)


router = APIRouter(prefix="/api/intel", tags=["intelligence"], dependencies=[Depends(_rate_limit_intel)])

# -- Cache --
_cache: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 300  # 5 minutes default


def _cache_get(key: str, ttl: int = _CACHE_TTL) -> Any | None:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None


def _cache_set(key: str, data: Any) -> None:
    # Cap cache size
    if len(_cache) > 500:
        oldest = sorted(_cache, key=lambda k: _cache[k]["ts"])[:100]
        for k in oldest:
            del _cache[k]
    _cache[key] = {"data": data, "ts": time.time()}


# ══════════════════════════════════════════
#  I1: SEC EDGAR Insider Trading
# ══════════════════════════════════════════

SEC_EDGAR_HEADERS = {
    "User-Agent": "MAXIA/1.0 (support@maxiaworld.app)",
    "Accept": "application/json",
}


@router.get("/insider-trades")
async def insider_trades(
    symbol: str = Query(..., description="Stock ticker (e.g. AAPL, TSLA, NVDA)"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Get recent insider trades from SEC EDGAR Form 4 filings.

    Free API, no key needed. 10 requests/second limit.
    Useful for tokenized stocks (xStocks/Ondo/Dinari).
    """
    symbol = symbol.upper().strip()
    cache_key = f"insider:{symbol}:{limit}"
    cached = _cache_get(cache_key, ttl=600)
    if cached:
        return cached

    client = get_http_client()
    try:
        # Step 1: Find CIK for ticker
        cik_resp = await client.get(
            f"https://efts.sec.gov/LATEST/search-index?q={symbol}&dateRange=custom&startdt=2024-01-01&forms=4",
            headers=SEC_EDGAR_HEADERS,
        )
        # Use company tickers endpoint
        tickers_resp = await client.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_EDGAR_HEADERS,
        )
        tickers_resp.raise_for_status()
        tickers = tickers_resp.json()

        cik = None
        company_name = ""
        for entry in tickers.values():
            if entry.get("ticker", "").upper() == symbol:
                cik = str(entry["cik_str"]).zfill(10)
                company_name = entry.get("title", "")
                break

        if not cik:
            return {
                "symbol": symbol,
                "trades": [],
                "error": f"Ticker '{symbol}' not found in SEC database",
            }

        # Step 2: Get recent Form 4 filings
        filings_resp = await client.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{cik}%22&forms=4&dateRange=custom&startdt=2024-01-01",
            headers=SEC_EDGAR_HEADERS,
        )

        # Step 3: Use EDGAR full-text search for insider transactions
        search_resp = await client.get(
            f"https://efts.sec.gov/LATEST/search-index?q={symbol}&forms=4",
            headers=SEC_EDGAR_HEADERS,
        )

        # Fallback: use submissions endpoint
        submissions_resp = await client.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=SEC_EDGAR_HEADERS,
        )
        submissions_resp.raise_for_status()
        submissions = submissions_resp.json()

        recent_filings = submissions.get("filings", {}).get("recent", {})
        forms = recent_filings.get("form", [])
        dates = recent_filings.get("filingDate", [])
        accessions = recent_filings.get("accessionNumber", [])
        descriptions = recent_filings.get("primaryDocDescription", [])

        trades: list[dict[str, Any]] = []
        for i, form in enumerate(forms):
            if form == "4" and i < len(dates):
                trades.append({
                    "form": "4",
                    "filing_date": dates[i] if i < len(dates) else "",
                    "accession": accessions[i] if i < len(accessions) else "",
                    "description": descriptions[i] if i < len(descriptions) else "Statement of changes in beneficial ownership",
                    "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=10",
                })
                if len(trades) >= limit:
                    break

        result = {
            "symbol": symbol,
            "company": company_name,
            "cik": cik,
            "trade_count": len(trades),
            "trades": trades,
            "source": "SEC EDGAR (free, public)",
            "note": "Form 4 filings — insider buys/sells. Useful for tokenized stock analysis.",
        }
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        logger.error("[INTEL] SEC EDGAR error for %s: %s", symbol, e)
        raise HTTPException(502, safe_error("SEC EDGAR insider trades"))


# ══════════════════════════════════════════
#  I2: NVD CVE Security Feed
# ══════════════════════════════════════════


@router.get("/cve-search")
async def cve_search(
    keyword: str = Query(..., description="Search keyword (e.g. solana, ethereum, nodejs)"),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """Search CVE vulnerabilities via NIST NVD API.

    Free, no key needed (50 req/30s with key, 5 req/30s without).
    """
    keyword = keyword.strip().lower()
    cache_key = f"cve:{keyword}:{limit}"
    cached = _cache_get(cache_key, ttl=1800)  # 30 min cache
    if cached:
        return cached

    client = get_http_client()
    try:
        params: dict[str, Any] = {
            "keywordSearch": keyword,
            "resultsPerPage": limit,
        }
        nvd_key = os.getenv("NVD_API_KEY", "")
        headers: dict[str, str] = {}
        if nvd_key:
            headers["apiKey"] = nvd_key

        resp = await client.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params=params,
            headers=headers,
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()

        vulnerabilities = []
        for item in data.get("vulnerabilities", [])[:limit]:
            cve = item.get("cve", {})
            desc_list = cve.get("descriptions", [])
            desc = next((d["value"] for d in desc_list if d.get("lang") == "en"), "")
            metrics = cve.get("metrics", {})
            cvss_v31 = metrics.get("cvssMetricV31", [{}])
            score = cvss_v31[0].get("cvssData", {}).get("baseScore", 0) if cvss_v31 else 0
            severity = cvss_v31[0].get("cvssData", {}).get("baseSeverity", "UNKNOWN") if cvss_v31 else "UNKNOWN"

            vulnerabilities.append({
                "id": cve.get("id", ""),
                "description": desc[:300],
                "score": score,
                "severity": severity,
                "published": cve.get("published", ""),
                "url": f"https://nvd.nist.gov/vuln/detail/{cve.get('id', '')}",
            })

        result = {
            "keyword": keyword,
            "total_results": data.get("totalResults", 0),
            "returned": len(vulnerabilities),
            "vulnerabilities": vulnerabilities,
            "source": "NIST NVD (free, public)",
        }
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        logger.error("[INTEL] NVD CVE error for %s: %s", keyword, e)
        raise HTTPException(502, safe_error("NVD CVE search"))


# ══════════════════════════════════════════
#  I3: GitHub Trending Repos
# ══════════════════════════════════════════


@router.get("/trending-repos")
async def trending_repos(
    topic: str = Query("ai", description="Topic: ai, crypto, blockchain, defi, solana, trading"),
    limit: int = Query(15, ge=1, le=50),
) -> dict[str, Any]:
    """Get trending GitHub repos by topic (created in last 30 days, sorted by stars).

    Free GitHub API, no key needed (60 req/h unauthenticated).
    """
    topic = topic.strip().lower()
    cache_key = f"trending:{topic}:{limit}"
    cached = _cache_get(cache_key, ttl=1800)  # 30 min cache
    if cached:
        return cached

    client = get_http_client()
    try:
        # Repos created in last 30 days with topic, sorted by stars
        import datetime
        since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        query = f"{topic} created:>{since}"

        gh_token = os.getenv("GITHUB_TOKEN", "")
        headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        resp = await client.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
            headers=headers,
        )
        # Retry without token if 401 (expired token)
        if resp.status_code == 401 and gh_token:
            headers.pop("Authorization", None)
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
                headers=headers,
            )
        resp.raise_for_status()
        data = resp.json()

        repos = []
        for item in data.get("items", [])[:limit]:
            repos.append({
                "name": item["full_name"],
                "description": (item.get("description") or "")[:200],
                "stars": item["stargazers_count"],
                "forks": item["forks_count"],
                "language": item.get("language", ""),
                "created": item["created_at"][:10],
                "url": item["html_url"],
                "topics": item.get("topics", [])[:5],
            })

        result = {
            "topic": topic,
            "period": "last 30 days",
            "count": len(repos),
            "repos": repos,
            "source": "GitHub API (free)",
        }
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        logger.error("[INTEL] GitHub trending error for %s: %s", topic, e)
        raise HTTPException(502, safe_error("GitHub trending repos"))


# ══════════════════════════════════════════
#  I4: Deep Research (Tavily)
# ══════════════════════════════════════════

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


@router.get("/research")
async def deep_research(
    query: str = Query(..., description="Research question (e.g. 'defi trends 2026')"),
    depth: str = Query("basic", description="basic or advanced"),
) -> dict[str, Any]:
    """Deep web research via Tavily API.

    Requires TAVILY_API_KEY env var. Free: 1000 requests/month.
    Falls back to a simple web search if no key.
    """
    query = query.strip()
    if not query:
        raise HTTPException(400, "query required")

    cache_key = f"research:{query}:{depth}"
    cached = _cache_get(cache_key, ttl=3600)  # 1h cache
    if cached:
        return cached

    client = get_http_client()

    if TAVILY_API_KEY:
        try:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": depth,
                    "max_results": 10,
                    "include_answer": True,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:300],
                    "score": r.get("score", 0),
                })

            result = {
                "query": query,
                "answer": data.get("answer", ""),
                "results": results,
                "source": "Tavily API",
            }
            _cache_set(cache_key, result)
            return result

        except Exception as e:
            logger.error("[INTEL] Tavily error: %s", e)
            # Fall through to fallback

    # Fallback: no Tavily key — return guidance
    return {
        "query": query,
        "answer": "",
        "results": [],
        "source": "none",
        "note": "Set TAVILY_API_KEY in .env for deep research (1000 free/month at tavily.com).",
    }


# ══════════════════════════════════════════
#  I5: Whale Enhanced (Helius / Solscan)
# ══════════════════════════════════════════

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")


@router.get("/whale-enhanced/{wallet}")
async def whale_enhanced(
    wallet: str,
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """Enhanced whale analysis for a Solana wallet using Helius.

    Shows recent large transactions, token holdings, and DeFi activity.
    Uses HELIUS_API_KEY if available, falls back to public Solana RPC.
    """
    import re
    if not re.match(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$', wallet):
        raise HTTPException(400, "Invalid Solana wallet address")

    cache_key = f"whale:{wallet}:{limit}"
    cached = _cache_get(cache_key, ttl=120)  # 2 min cache
    if cached:
        return cached

    client = get_http_client()
    result: dict[str, Any] = {"wallet": wallet, "source": "Solana RPC"}

    # Helius enhanced API (if key available)
    if HELIUS_API_KEY:
        try:
            # Get parsed transaction history
            tx_resp = await client.get(
                f"https://api.helius.xyz/v0/addresses/{wallet}/transactions",
                params={"api-key": HELIUS_API_KEY, "limit": limit},
                timeout=15.0,
            )
            if tx_resp.status_code == 200:
                txs = tx_resp.json()
                result["transactions"] = [
                    {
                        "signature": tx.get("signature", "")[:20] + "...",
                        "type": tx.get("type", "UNKNOWN"),
                        "description": tx.get("description", "")[:200],
                        "timestamp": tx.get("timestamp", 0),
                        "fee": tx.get("fee", 0),
                        "source": tx.get("source", ""),
                    }
                    for tx in (txs if isinstance(txs, list) else [])[:limit]
                ]
                result["source"] = "Helius API"

            # Get balances
            bal_resp = await client.get(
                f"https://api.helius.xyz/v0/addresses/{wallet}/balances",
                params={"api-key": HELIUS_API_KEY},
                timeout=10.0,
            )
            if bal_resp.status_code == 200:
                bal_data = bal_resp.json()
                result["sol_balance"] = bal_data.get("nativeBalance", 0) / 1e9
                tokens = bal_data.get("tokens", [])
                result["token_count"] = len(tokens)
                result["top_tokens"] = [
                    {"mint": t.get("mint", "")[:12] + "...", "amount": t.get("amount", 0)}
                    for t in sorted(tokens, key=lambda x: x.get("amount", 0), reverse=True)[:10]
                ]

        except Exception as e:
            logger.warning("[INTEL] Helius error for %s: %s", wallet[:8], e)

    # Fallback: public Solana RPC
    if "transactions" not in result:
        try:
            rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            rpc_resp = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet, {"limit": limit}],
                },
                timeout=10.0,
            )
            if rpc_resp.status_code == 200:
                sigs = rpc_resp.json().get("result", [])
                result["transactions"] = [
                    {
                        "signature": s.get("signature", "")[:20] + "...",
                        "slot": s.get("slot", 0),
                        "timestamp": s.get("blockTime", 0),
                        "err": s.get("err"),
                    }
                    for s in sigs[:limit]
                ]
                result["source"] = "Solana RPC (public)"
        except Exception as e:
            logger.warning("[INTEL] Solana RPC fallback error: %s", e)
            result["transactions"] = []

    result["transaction_count"] = len(result.get("transactions", []))
    _cache_set(cache_key, result)
    return result


# ══════════════════════════════════════════
#  I6: Company Lookup (OpenCorporates)
# ══════════════════════════════════════════


@router.get("/company-lookup")
async def company_lookup(
    name: str = Query(..., description="Company name to search"),
    jurisdiction: str = Query("", description="Country code (us, gb, fr, de...)"),
) -> dict[str, Any]:
    """Search company registry data via OpenCorporates API.

    Free tier: 50 requests/month, no key needed.
    """
    name = name.strip()
    cache_key = f"company:{name}:{jurisdiction}"
    cached = _cache_get(cache_key, ttl=86400)  # 24h cache
    if cached:
        return cached

    client = get_http_client()
    try:
        params: dict[str, Any] = {"q": name, "per_page": 10}
        if jurisdiction:
            params["jurisdiction_code"] = jurisdiction.lower()

        resp = await client.get(
            "https://api.opencorporates.com/v0.4/companies/search",
            params=params,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

        companies = []
        for item in data.get("results", {}).get("companies", []):
            c = item.get("company", {})
            companies.append({
                "name": c.get("name", ""),
                "jurisdiction": c.get("jurisdiction_code", ""),
                "company_number": c.get("company_number", ""),
                "status": c.get("current_status", ""),
                "type": c.get("company_type", ""),
                "incorporated": c.get("incorporation_date", ""),
                "address": c.get("registered_address_in_full", ""),
                "url": c.get("opencorporates_url", ""),
            })

        result = {
            "query": name,
            "jurisdiction": jurisdiction or "all",
            "count": len(companies),
            "companies": companies,
            "source": "OpenCorporates (free tier)",
        }
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        logger.error("[INTEL] OpenCorporates error for %s: %s", name, e)
        raise HTTPException(502, safe_error("Company lookup"))


# ══════════════════════════════════════════
#  I7: Competitive Scanner
# ══════════════════════════════════════════

_COMPETITORS = [
    {"name": "OpenClawnch", "url": "https://openclawnch.com", "type": "agent_platform"},
    {"name": "Virtuals Protocol", "url": "https://virtuals.io", "type": "agent_token"},
    {"name": "Jupiter", "url": "https://jup.ag", "type": "dex"},
    {"name": "Unibot", "url": "https://unibot.app", "type": "trading_bot"},
    {"name": "BonkBot", "url": "https://bonkbot.io", "type": "trading_bot"},
    {"name": "3Commas", "url": "https://3commas.io", "type": "trading_bot"},
    {"name": "Akash Network", "url": "https://akash.network", "type": "gpu"},
    {"name": "Render Network", "url": "https://render.com", "type": "gpu"},
]


@router.get("/competitors")
async def competitive_scan() -> dict[str, Any]:
    """Quick health check of known competitors (response time, status).

    Uses HEAD requests only — lightweight, no scraping.
    """
    cache_key = "competitors:scan"
    cached = _cache_get(cache_key, ttl=600)  # 10 min cache
    if cached:
        return cached

    client = get_http_client()
    results = []

    for comp in _COMPETITORS:
        entry: dict[str, Any] = {
            "name": comp["name"],
            "url": comp["url"],
            "type": comp["type"],
        }
        try:
            start = time.time()
            resp = await client.head(comp["url"], timeout=8.0)
            entry["status"] = resp.status_code
            entry["response_ms"] = int((time.time() - start) * 1000)
            entry["online"] = resp.status_code < 400
        except Exception:
            entry["status"] = 0
            entry["response_ms"] = -1
            entry["online"] = False

        results.append(entry)

    result = {
        "competitors": len(results),
        "online": sum(1 for r in results if r["online"]),
        "results": results,
        "scanned_at": int(time.time()),
    }
    _cache_set(cache_key, result)
    return result


# ══════════════════════════════════════════
#  I8: SEO Domain Checker
# ══════════════════════════════════════════


@router.get("/seo-check")
async def seo_check(
    domain: str = Query("maxiaworld.app", description="Domain to check"),
) -> dict[str, Any]:
    """Basic SEO health check for a domain.

    Checks: DNS resolution, HTTPS, response time, headers, robots.txt, sitemap.
    Free, no external API needed.
    """
    domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
    cache_key = f"seo:{domain}"
    cached = _cache_get(cache_key, ttl=1800)
    if cached:
        return cached

    client = get_http_client()
    checks: dict[str, Any] = {"domain": domain}

    # 1. Homepage
    try:
        start = time.time()
        resp = await client.get(f"https://{domain}", timeout=10.0)
        checks["https"] = True
        checks["status"] = resp.status_code
        checks["response_ms"] = int((time.time() - start) * 1000)
        checks["content_length"] = len(resp.content)

        # Check security headers
        headers = dict(resp.headers)
        checks["headers"] = {
            "hsts": "strict-transport-security" in headers,
            "csp": "content-security-policy" in headers,
            "x_frame": "x-frame-options" in headers,
            "x_content_type": "x-content-type-options" in headers,
        }
    except Exception as e:
        checks["https"] = False
        checks["error"] = str(type(e).__name__)

    # 2. robots.txt
    try:
        robots_resp = await client.get(f"https://{domain}/robots.txt", timeout=5.0)
        checks["robots_txt"] = robots_resp.status_code == 200
        if robots_resp.status_code == 200:
            checks["robots_size"] = len(robots_resp.content)
    except Exception:
        checks["robots_txt"] = False

    # 3. sitemap.xml
    try:
        sitemap_resp = await client.get(f"https://{domain}/sitemap.xml", timeout=5.0)
        checks["sitemap"] = sitemap_resp.status_code == 200
    except Exception:
        checks["sitemap"] = False

    # 4. favicon
    try:
        fav_resp = await client.get(f"https://{domain}/favicon.ico", timeout=5.0)
        checks["favicon"] = fav_resp.status_code == 200
    except Exception:
        checks["favicon"] = False

    # Score
    score = 0
    if checks.get("https"):
        score += 20
    if checks.get("status") == 200:
        score += 10
    if checks.get("response_ms", 9999) < 2000:
        score += 10
    if checks.get("robots_txt"):
        score += 15
    if checks.get("sitemap"):
        score += 15
    if checks.get("favicon"):
        score += 5
    for h_val in checks.get("headers", {}).values():
        if h_val:
            score += 5
    checks["seo_score"] = min(score, 100)
    checks["source"] = "MAXIA SEO checker (free)"

    _cache_set(cache_key, checks)
    return checks
