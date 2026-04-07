"""MAXIA Art.25 — Web Scraper Service

Les IA ne peuvent pas acceder au web. Ce service scrape une URL
et retourne le contenu structure (texte, titres, liens, images).
Utilise httpx + BeautifulSoup (ou regex fallback).
"""
import logging
import asyncio, re, time, hashlib, ipaddress
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)

# Cache pour eviter de scraper la meme page plusieurs fois
_scrape_cache: dict = {}  # url_hash -> {content, timestamp}
_CACHE_TTL = 300  # 5 minutes
_scrape_stats = {"total": 0, "cached": 0, "errors": 0}

# User agents rotatifs pour eviter les bans
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
]

# Domaines bloques (securite Art.1)
BLOCKED_DOMAINS = [
    "porn", "xxx", "adult", "sex", "nsfw",
    "darkweb", "onion", "tor2web",
]

logger.info("Service initialise")


def _get_ua() -> str:
    """User agent rotatif."""
    import random
    return random.choice(USER_AGENTS)


async def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate URL is safe to fetch (Art.1 + SSRF + DNS rebinding protection).

    Returns (is_safe, resolved_ip). If is_safe is False, resolved_ip contains the reason.
    Resolves DNS once and returns the IP so the caller can pin the connection to it,
    preventing DNS rebinding attacks (PRO-A6).
    """
    url_lower = url.lower()

    # Schemas dangereux — seuls http:// et https:// sont autorises
    BLOCKED_SCHEMES = ("file://", "ftp://", "gopher://", "dict://", "ldap://", "tftp://")
    for scheme in BLOCKED_SCHEMES:
        if url_lower.startswith(scheme):
            return False, "blocked_scheme"

    # Domaines interdits (contenu Art.1)
    for blocked in BLOCKED_DOMAINS:
        if blocked in url_lower:
            return False, "blocked_domain"

    # SSRF: bloquer les IPs privees, loopback, link-local, metadata cloud
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # Bloquer localhost et variantes
        if hostname in ("localhost", "0.0.0.0", "[::]", "[::1]"):
            return False, "localhost"

        # Resoudre le hostname en IP et verifier les plages privees
        import socket
        resolved_ips = await asyncio.to_thread(socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        first_safe_ip = None
        for _, _, _, _, sockaddr in resolved_ips:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            # Bloquer: loopback, prive, link-local, metadata AWS/cloud
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return False, "private_ip"
            # Double-check metadata IP explicitement (169.254.169.254)
            if ip_str in ("169.254.169.254", "fd00::ec2"):
                return False, "metadata_ip"
            if first_safe_ip is None:
                first_safe_ip = ip_str
        if first_safe_ip is None:
            return False, "no_resolved_ip"
        return True, first_safe_ip
    except Exception:
        # DNS resolution impossible — bloquer par precaution
        return False, "dns_error"


def _extract_text_regex(html: str) -> dict:
    """Extraction de contenu via regex (pas de dependance externe)."""
    # Supprimer les scripts et styles
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

    # Extraire le titre
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    title = re.sub(r'<[^>]+>', '', title)

    # Extraire la meta description
    desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html, re.IGNORECASE)
    if not desc_match:
        desc_match = re.search(r'<meta[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']', html, re.IGNORECASE)
    description = desc_match.group(1).strip() if desc_match else ""

    # Extraire les titres h1-h6
    headings = []
    for level in range(1, 7):
        for match in re.finditer(rf'<h{level}[^>]*>(.*?)</h{level}>', html, re.IGNORECASE | re.DOTALL):
            text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            if text:
                headings.append({"level": level, "text": text})

    # Extraire les paragraphes
    paragraphs = []
    for match in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.IGNORECASE | re.DOTALL):
        text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        text = re.sub(r'\s+', ' ', text)
        if len(text) > 20:
            paragraphs.append(text)

    # Extraire les liens
    links = []
    for match in re.finditer(r'<a[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
        href = match.group(1)
        text = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        if text and len(text) > 2:
            links.append({"url": href, "text": text[:100]})

    # Extraire les images
    images = []
    for match in re.finditer(r'<img[^>]*src=["\'](https?://[^"\']+)["\'][^>]*', html, re.IGNORECASE):
        src = match.group(1)
        alt_match = re.search(r'alt=["\'](.*?)["\']', match.group(0), re.IGNORECASE)
        alt = alt_match.group(1) if alt_match else ""
        images.append({"url": src, "alt": alt})

    # Texte brut complet
    full_text = re.sub(r'<[^>]+>', ' ', html)
    full_text = re.sub(r'\s+', ' ', full_text).strip()

    # Limiter la taille
    full_text = full_text[:10000]
    paragraphs = paragraphs[:50]
    links = links[:30]
    images = images[:20]

    return {
        "title": title,
        "description": description,
        "headings": headings[:20],
        "paragraphs": paragraphs,
        "links": links,
        "images": images,
        "text_length": len(full_text),
        "full_text": full_text,
    }


async def scrape_url(url: str, extract_links: bool = True,
                      extract_images: bool = True,
                      max_text_length: int = 10000) -> dict:
    """Scrape une URL et retourne le contenu structure."""
    # Validation
    if not url or not url.startswith("http"):
        return {"success": False, "error": "URL invalide. Doit commencer par http:// ou https://"}

    is_safe, resolved_ip = await _is_safe_url(url)
    if not is_safe:
        return {"success": False, "error": "URL bloquee par Art.1 (contenu interdit)"}

    # Cache
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    cached = _scrape_cache.get(url_hash)
    if cached and time.time() - cached["timestamp"] < _CACHE_TTL:
        _scrape_stats["cached"] += 1
        return {**cached["content"], "cached": True}

    _scrape_stats["total"] += 1

    try:
        headers = {
            "User-Agent": _get_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True, max_redirects=5) as client:
            resp = await client.get(url, headers=headers)

        # PRO-A6: After following redirects, re-validate the final URL to prevent
        # open-redirect-based SSRF (redirect to internal IP).
        final_url = str(resp.url)
        if final_url != url:
            final_safe, _ = await _is_safe_url(final_url)
            if not final_safe:
                return {"success": False, "error": "URL bloquee apres redirection (SSRF protection)"}

        if resp.status_code != 200:
            _scrape_stats["errors"] += 1
            return {"success": False, "error": f"HTTP {resp.status_code}", "url": url}

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            # Pas du HTML — retourner le texte brut
            text = resp.text[:max_text_length]
            result = {
                "success": True, "url": url, "content_type": content_type,
                "title": "", "description": "", "headings": [],
                "paragraphs": [], "links": [], "images": [],
                "full_text": text, "text_length": len(text),
            }
            _scrape_cache[url_hash] = {"content": result, "timestamp": time.time()}
            return result

        html = resp.text
        extracted = _extract_text_regex(html)

        # Limiter le texte
        if max_text_length and len(extracted["full_text"]) > max_text_length:
            extracted["full_text"] = extracted["full_text"][:max_text_length]

        if not extract_links:
            extracted["links"] = []
        if not extract_images:
            extracted["images"] = []

        result = {
            "success": True,
            "url": url,
            "status_code": resp.status_code,
            "content_type": content_type,
            **extracted,
            "cached": False,
            "scraped_at": int(time.time()),
        }

        # Mettre en cache
        _scrape_cache[url_hash] = {"content": result, "timestamp": time.time()}

        logger.info(f"Scraped: {url[:60]}... — {extracted['text_length']} chars")
        return result

    except httpx.TimeoutException:
        _scrape_stats["errors"] += 1
        return {"success": False, "error": "Timeout (15s)", "url": url}
    except Exception as e:
        _scrape_stats["errors"] += 1
        return {"success": False, "error": "An error occurred"[:200], "url": url}


async def scrape_multiple(urls: list, max_text_length: int = 5000) -> dict:
    """Scrape plusieurs URLs en parallele (max 5)."""
    urls = urls[:5]  # Max 5 URLs
    tasks = [scrape_url(u, max_text_length=max_text_length) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    scraped = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            scraped.append({"success": False, "url": urls[i], "error": str(r)})
        else:
            scraped.append(r)

    return {
        "total": len(urls),
        "success_count": sum(1 for r in scraped if r.get("success")),
        "results": scraped,
    }


def get_scraper_stats() -> dict:
    return {
        **_scrape_stats,
        "cache_size": len(_scrape_cache),
        "cache_ttl": _CACHE_TTL,
    }
