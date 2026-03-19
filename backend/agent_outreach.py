"""MAXIA Agent Outreach — Cold Calling AI Agents

Autonomous agent that discovers other AI agents on public registries
and proposes MAXIA marketplace integration.

Contacts agents via:
1. A2A agent cards (/.well-known/agent.json)
2. Public API endpoints
3. MCP server discovery
4. Registry listings (Bittensor, AutoGPT hub)

Does NOT spam. Contacts max 5 agents/day. Tracks who was contacted.
"""
import asyncio, time, os, json, hashlib
import httpx

MAXIA_URL = "https://maxiaworld.app"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

_outreach_file = "/tmp/maxia_outreach_log.json"
_MAX_PER_DAY = 5


# ══════════════════════════════════════════
# Known agent registries to scan
# ══════════════════════════════════════════

AGENT_REGISTRIES = [
    # Public A2A agent card URLs to check
    {"type": "a2a", "urls": [
        "https://api.dain.org/.well-known/agent.json",
        "https://api.sendai.fun/.well-known/agent.json",
        "https://agent.virtuals.io/.well-known/agent.json",
    ]},
]

# ══════════════════════════════════════════
# Outreach message generator
# ══════════════════════════════════════════

async def _generate_outreach_message(agent_name: str, agent_capabilities: list) -> str:
    """Generate a personalized outreach message using Groq."""
    if not GROQ_API_KEY:
        return _default_message(agent_name)

    caps = ", ".join(agent_capabilities[:5]) if agent_capabilities else "unknown"
    prompt = (
        f"Write a SHORT API message (max 150 chars) from MAXIA to agent '{agent_name}'.\n"
        f"Their capabilities: {caps}\n"
        f"MAXIA is an AI-to-AI marketplace on Solana. Agents sell services to agents.\n"
        f"Propose: their agent lists services on MAXIA to earn USDC.\n"
        f"Tone: technical, peer-to-peer, not salesy.\n"
        f"Include: maxiaworld.app\n"
        f"Plain text, no JSON."
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass

    return _default_message(agent_name)


def _default_message(agent_name: str) -> str:
    return (
        f"Hey {agent_name} — MAXIA is an AI-to-AI marketplace on Solana. "
        f"Your agent can list services and earn USDC when other agents buy. "
        f"MCP server + A2A discovery. maxiaworld.app"
    )


# ══════════════════════════════════════════
# Discovery — find other agents
# ══════════════════════════════════════════

async def discover_a2a_agents() -> list:
    """Discover agents via A2A agent cards."""
    found = []
    for registry in AGENT_REGISTRIES:
        if registry["type"] == "a2a":
            for url in registry["urls"]:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        r = await client.get(url)
                        if r.status_code == 200:
                            data = r.json()
                            found.append({
                                "name": data.get("name", "unknown"),
                                "url": url.replace("/.well-known/agent.json", ""),
                                "capabilities": [s.get("name", "") for s in data.get("capabilities", data.get("services", []))],
                                "contact_url": data.get("contact_url", data.get("endpoints", {}).get("register", "")),
                                "source": "a2a",
                            })
                except Exception:
                    pass
    return found


async def discover_via_search() -> list:
    """Discover agents by checking known endpoints."""
    known_endpoints = [
        {"name": "SendAI", "url": "https://api.sendai.fun", "type": "toolkit"},
        {"name": "Crossmint", "url": "https://www.crossmint.com/api", "type": "wallet"},
        {"name": "Helius", "url": "https://api.helius.xyz", "type": "rpc"},
    ]
    found = []
    for ep in known_endpoints:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(ep["url"], follow_redirects=True)
                if r.status_code in [200, 301, 302, 403]:
                    found.append({
                        "name": ep["name"],
                        "url": ep["url"],
                        "capabilities": [ep["type"]],
                        "source": "known_endpoint",
                    })
        except Exception:
            pass
    return found


# ══════════════════════════════════════════
# Contact — propose MAXIA to discovered agents
# ══════════════════════════════════════════

def _load_outreach_log() -> dict:
    try:
        with open(_outreach_file) as f:
            return json.load(f)
    except Exception:
        return {"contacted": {}, "total": 0}


def _save_outreach_log(log: dict):
    try:
        with open(_outreach_file, "w") as f:
            json.dump(log, f)
    except Exception:
        pass


def _agent_id(agent: dict) -> str:
    return hashlib.md5(agent.get("url", agent.get("name", "")).encode()).hexdigest()


async def contact_agent_via_a2a(agent: dict) -> dict:
    """Send a message to an agent via their API (if they accept messages)."""
    message = await _generate_outreach_message(
        agent.get("name", "Agent"),
        agent.get("capabilities", []),
    )

    result = {
        "agent": agent.get("name", ""),
        "url": agent.get("url", ""),
        "message": message,
        "status": "logged",
    }

    # Try to send via their /execute or /message endpoint if available
    contact_url = agent.get("contact_url", "")
    if contact_url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(contact_url, json={
                    "from": "MAXIA",
                    "from_url": MAXIA_URL,
                    "message": message,
                    "type": "partnership_proposal",
                })
                if r.status_code in [200, 201, 202]:
                    result["status"] = "sent"
                else:
                    result["status"] = f"http_{r.status_code}"
        except Exception as e:
            result["status"] = f"error: {str(e)[:50]}"

    return result


# ══════════════════════════════════════════
# Main outreach loop
# ══════════════════════════════════════════

async def run_outreach_cycle() -> dict:
    """Run one outreach cycle. Max 5 contacts per day."""
    log = _load_outreach_log()
    today = time.strftime("%Y-%m-%d")
    today_count = sum(1 for v in log["contacted"].values() if v.get("date") == today)

    if today_count >= _MAX_PER_DAY:
        return {"status": "daily_limit_reached", "contacted_today": today_count}

    # Discover agents
    agents = await discover_a2a_agents()
    agents += await discover_via_search()

    contacted = []
    for agent in agents:
        aid = _agent_id(agent)
        if aid in log["contacted"]:
            continue  # Already contacted
        if today_count >= _MAX_PER_DAY:
            break

        result = await contact_agent_via_a2a(agent)
        log["contacted"][aid] = {
            "name": agent.get("name", ""),
            "url": agent.get("url", ""),
            "date": today,
            "status": result["status"],
        }
        log["total"] += 1
        today_count += 1
        contacted.append(result)
        await asyncio.sleep(5)  # Be polite

    _save_outreach_log(log)

    return {
        "discovered": len(agents),
        "contacted_today": today_count,
        "new_contacts": len(contacted),
        "contacts": contacted,
        "total_all_time": log["total"],
    }


async def run_outreach_bot():
    """Background loop — runs outreach once per day."""
    print("[Outreach] Agent outreach bot started")
    while True:
        try:
            result = await run_outreach_cycle()
            if result.get("new_contacts", 0) > 0:
                print(f"[Outreach] Contacted {result['new_contacts']} new agents")
        except Exception as e:
            print(f"[Outreach] Error: {e}")
        await asyncio.sleep(86400)  # Once per day


def get_stats() -> dict:
    log = _load_outreach_log()
    return {
        "total_contacted": log.get("total", 0),
        "agents": list(log.get("contacted", {}).values())[-10:],
    }
