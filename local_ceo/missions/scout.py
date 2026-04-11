"""Mission 17 — Scout AI: scan registries, score agents, propose to Alexis.

Discovers AI agents across registries (Virtuals, Agentverse, Smithery, ElizaOS, GitHub),
scores them for MAXIA relevance, and sends a validation email to Alexis.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime

import httpx

from llm import llm
from agents import CEO_SYSTEM_PROMPT, AI_REGISTRIES
from scheduler import send_mail

log = logging.getLogger("ceo")

_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(__file__))  # local_ceo/
_SCOUT_FILE = os.path.join(_LOCAL_CEO_DIR, "scout_discoveries.json")
_SCOUT_PENDING_FILE = os.path.join(_LOCAL_CEO_DIR, "scout_pending_contacts.json")


def _load_scout_data() -> dict:
    default = {"discovered": {}, "contacted": [], "last_scan": ""}
    try:
        if os.path.exists(_SCOUT_FILE):
            with open(_SCOUT_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return default


def _save_scout_data(data: dict) -> None:
    try:
        with open(_SCOUT_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    except Exception as e:
        log.error("[SCOUT] Save error: %s", e)


def _load_pending_contacts() -> list:
    try:
        if os.path.exists(_SCOUT_PENDING_FILE):
            with open(_SCOUT_PENDING_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return []


def _save_pending_contacts(pending: list) -> None:
    try:
        with open(_SCOUT_PENDING_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(pending, indent=2, default=str, ensure_ascii=False))
    except Exception:
        pass


async def mission_scout_scan(mem: dict, actions: dict) -> None:
    """Scan les registries AI pour trouver des agents, propose a Alexis par mail."""
    if actions["counts"].get("scout_done", 0) >= 1:
        return

    scout = _load_scout_data()
    known_ids = set(scout.get("discovered", {}).keys())
    contacted_ids = set(scout.get("contacted", []))
    new_agents = []

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for registry in AI_REGISTRIES:
            try:
                headers = {"User-Agent": "MAXIA-Scout/1.0"}
                if registry.get("method") == "POST":
                    resp = await client.post(
                        registry["url"], json=registry.get("post_body", {}), headers=headers,
                    )
                else:
                    if "github.com" in registry["url"]:
                        headers["Accept"] = "application/vnd.github.v3+json"
                    resp = await client.get(registry["url"], headers=headers)
                if resp.status_code != 200:
                    log.warning("[SCOUT] %s HTTP %d", registry["name"], resp.status_code)
                    continue

                data = resp.json()
                fmt = registry.get("format", "")

                # Parser selon le format du registry
                agents_list = _parse_registry_response(data, fmt)

                found_count = 0
                for agent in agents_list[:20]:
                    if not isinstance(agent, dict):
                        continue
                    # Extraire un ID unique
                    agent_id = str(
                        agent.get("id", "") or agent.get("address", "") or
                        agent.get("name", "") or agent.get("service_id", "")
                    )
                    if not agent_id or agent_id in known_ids or agent_id in contacted_ids:
                        continue

                    # Extraire les infos utiles
                    name = agent.get("name", agent.get("title", agent_id))
                    description = (agent.get("description", agent.get("desc", "")) or "")[:200]
                    url = agent.get("url", agent.get("homepage", agent.get("html_url", agent.get("api_url", ""))))
                    # Construire l'URL si absente selon le registry
                    if not url:
                        url = _build_agent_url(registry["name"], agent)
                    owner = agent.get("owner", agent.get("author", agent.get("creator", "")))
                    if isinstance(owner, dict):
                        owner = owner.get("login", owner.get("name", ""))

                    new_agents.append({
                        "id": agent_id,
                        "name": name,
                        "description": description,
                        "url": url or "",
                        "owner": str(owner or ""),
                        "registry": registry["name"],
                        "chain": registry["chain"],
                        "tier": registry.get("tier", "live"),
                        "discovered_at": datetime.now().isoformat(),
                    })

                    # Sauvegarder dans les decouvertes
                    scout["discovered"][agent_id] = {
                        "name": name, "registry": registry["name"],
                        "chain": registry["chain"], "ts": time.time(),
                    }
                    found_count += 1

                log.info("[SCOUT] %s: %d new agents (scanned %d)",
                         registry["name"], found_count, len(agents_list[:20]))
                await asyncio.sleep(2)  # Politesse entre registries

            except Exception as e:
                log.error("[SCOUT] %s error: %s", registry["name"], str(e)[:60])

    scout["last_scan"] = datetime.now().isoformat()
    _save_scout_data(scout)

    if not new_agents:
        log.info("[SCOUT] Aucun nouvel agent trouve")
        actions["counts"]["scout_done"] = 1
        return

    # Diversifier les sources — priorite aux agents live (contactables par API)
    by_registry = {}
    for a in new_agents:
        by_registry.setdefault(a["registry"], []).append(a)
    diversified = []
    per_source = max(2, 10 // len(by_registry)) if by_registry else 10
    for reg_agents in by_registry.values():
        diversified.extend(reg_agents[:per_source])
    live = [a for a in diversified if a.get("tier") == "live"]
    discovery = [a for a in diversified if a.get("tier") != "live"]
    candidates = (live + discovery)[:10]

    # Scorer et generer les messages de contact via LLM
    scored_agents = await _score_candidates(candidates)

    # Sauvegarder comme pending (en attente de validation Alexis)
    pending = _load_pending_contacts()
    for agent in scored_agents:
        agent["status"] = "pending"
        pending.append(agent)
    _save_pending_contacts(pending)

    # Envoyer le mail a Alexis pour validation
    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — Scout AI du {today}\n"
    body += f"{len(scored_agents)} agents IA decouverts sur {len(AI_REGISTRIES)} registries\n\n"
    body += "Pour contacter un agent, reponds a ce mail avec les numeros.\n"
    body += "Exemple: GO 1, 3, 5\n\n"

    for i, agent in enumerate(scored_agents, 1):
        body += f"--- Agent #{i} — Score: {agent['score']}/10 ---\n"
        body += f"  Nom: {agent['name']}\n"
        body += f"  Registry: {agent['registry']} ({agent['chain']})\n"
        body += f"  Description: {agent['description']}\n"
        if agent.get("url"):
            body += f"  URL: {agent['url']}\n"
        if agent.get("owner"):
            body += f"  Owner: {agent['owner']}\n"
        body += f"  Message propose:\n  \"{agent['contact_message']}\"\n"
        body += f"  Methode: {agent['contact_method']}\n\n"

    body += "--- INSTRUCTIONS ---\n"
    body += "Reponds GO <numeros> pour autoriser le contact.\n"
    body += "Reponds SKIP pour ignorer tous.\n"
    body += "Les agents non contactes seront reproposés demain.\n"

    await send_mail(f"[MAXIA CEO] Scout: {len(scored_agents)} agents IA trouves - {today}", body)
    actions["counts"]["scout_done"] = 1
    log.info("[SCOUT] %d agents trouves, mail envoye pour validation", len(scored_agents))


def _parse_registry_response(data, fmt: str) -> list:
    """Parse registry API response into a list of agent dicts."""
    agents_list = []
    if fmt == "elizaos":
        # ElizaOS: dict {"@package-name": "github:owner/repo"}
        for pkg_name, pkg_ref in list(data.items())[:20]:
            if isinstance(pkg_ref, str) and "github:" in pkg_ref:
                owner_repo = pkg_ref.replace("github:", "")
                agents_list.append({
                    "id": pkg_name,
                    "name": pkg_name.split("/")[-1],
                    "description": f"ElizaOS plugin: {pkg_name}",
                    "url": f"https://github.com/{owner_repo}",
                    "owner": owner_repo.split("/")[0] if "/" in owner_repo else "",
                })
    elif fmt == "github":
        # GitHub search: {"items": [...]}
        for repo in data.get("items", [])[:20]:
            agents_list.append({
                "id": f"gh:{repo.get('full_name', '')}",
                "name": repo.get("name", ""),
                "description": (repo.get("description") or "")[:200],
                "url": repo.get("html_url", ""),
                "owner": repo.get("owner", {}).get("login", ""),
            })
    elif fmt == "smithery":
        # Smithery: {"servers": [{displayName, description, homepage, qualifiedName, ...}]}
        srv_list = (
            data.get("servers", []) if isinstance(data, dict)
            else data if isinstance(data, list)
            else []
        )
        for srv in srv_list[:20]:
            if not isinstance(srv, dict):
                continue
            qname = srv.get("qualifiedName", srv.get("id", ""))
            agents_list.append({
                "id": f"smithery:{qname}",
                "name": srv.get("displayName", qname),
                "description": (srv.get("description") or "")[:200],
                "url": srv.get("homepage") or f"https://smithery.ai/server/{qname}",
                "owner": srv.get("namespace", ""),
            })
    elif isinstance(data, list):
        agents_list = data
    elif isinstance(data, dict):
        for key in ("agents", "services", "results", "data", "items"):
            if key in data and isinstance(data[key], list):
                agents_list = data[key]
                break
    return agents_list


def _build_agent_url(registry_name: str, agent: dict) -> str:
    """Build agent URL from registry-specific patterns."""
    if "Virtuals" in registry_name:
        return f"https://app.virtuals.io/virtuals/{agent.get('id', '')}"
    if "Agentverse" in registry_name:
        addr = agent.get("address", "")
        return f"https://agentverse.ai/agents/{addr}" if addr else ""
    if "Smithery" in registry_name:
        qname = agent.get("qualifiedName", agent.get("id", ""))
        return f"https://smithery.ai/server/{qname}"
    return ""


async def _score_candidates(candidates: list) -> list:
    """Score and generate contact messages for candidate agents."""
    scored_agents = []
    for agent in candidates:
        result = await llm(
            f"Tu es le CEO de MAXIA (marketplace AI-to-AI, 14 blockchains, escrow USDC on-chain).\n\n"
            f"Agent IA decouvert:\n"
            f"  Nom: {agent['name']}\n"
            f"  Description: {agent['description']}\n"
            f"  Registry: {agent['registry']}\n"
            f"  Chain: {agent['chain']}\n"
            f"  URL: {agent['url']}\n\n"
            f"SCORING RULES (strict):\n"
            f"- Score 8-10: Agent AUTONOME qui execute des taches (trading, data, code, DeFi, infra) et pourrait VENDRE ou ACHETER des services sur MAXIA\n"
            f"- Score 5-7: Agent technique avec potentiel d'integration (SDK, framework, tool)\n"
            f"- Score 1-4: Bot social, influenceur virtuel, personnalite IA, chatbot, mascotte — PAS pertinent pour un marketplace de SERVICES\n"
            f"- Si la description mentionne 'influencer', 'sing', 'dance', 'personality', 'waifu', 'companion' -> score MAX 3\n\n"
            f"1. Score de pertinence (1-10)\n"
            f"2. Message d'invitation EN ANGLAIS (max 500 chars): professionnel, explique ce que MAXIA apporte a CET agent specifiquement\n"
            f"3. Methode de contact: api_post (si URL API), email, ou manual\n\n"
            f"Format STRICT (pas d'autre texte):\n"
            f"SCORE|<nombre>\n"
            f"MSG|<message en anglais>\n"
            f"METHOD|<methode>",
            system=CEO_SYSTEM_PROMPT,
            max_tokens=300,
        )

        score = 5
        msg = ""
        method = "manual"
        if result:
            for line in result.strip().split("\n"):
                line = line.strip()
                if line.startswith("SCORE|"):
                    try:
                        score = int(line.split("|")[1].strip())
                    except Exception:
                        pass
                elif line.startswith("MSG|"):
                    msg = line.split("|", 1)[1].strip()[:500]
                elif line.startswith("METHOD|"):
                    method = line.split("|", 1)[1].strip().lower()

        agent["score"] = max(1, min(10, score))
        agent["contact_message"] = msg
        agent["contact_method"] = method
        scored_agents.append(agent)

    # Trier par score descendant
    scored_agents.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_agents


async def mission_scout_execute_approved(mem: dict) -> None:
    """Verifie si Alexis a repondu GO et contacte les agents approuves."""
    try:
        from email_manager import read_inbox
        emails = await read_inbox(max_emails=10)
    except Exception:
        return

    pending = _load_pending_contacts()
    if not pending:
        return

    scout = _load_scout_data()
    answered_ids = set(mem.get("emails_answered", []))

    for em in emails:
        msg_id = em.get("message_id", em.get("uid", ""))
        if msg_id in answered_ids:
            continue

        subject = em.get("subject", "").lower()
        body_text = em.get("body", "").upper()
        from_addr = em.get("from_addr", "").lower()

        if "majorel" not in from_addr and "maxia" not in from_addr:
            continue
        if "scout" not in subject:
            continue

        # Chercher "GO 1, 3, 5" dans le body
        if "GO" not in body_text and "SKIP" not in body_text:
            continue

        mem.setdefault("emails_answered", []).append(msg_id)

        if "SKIP" in body_text:
            # Marquer tous comme skipped
            for p in pending:
                p["status"] = "skipped"
            _save_pending_contacts([])
            log.info("[SCOUT] Alexis a SKIP tous les agents")
            continue

        # Parser les numeros apres GO
        import re
        numbers = re.findall(r'\d+', body_text.split("GO")[1] if "GO" in body_text else "")
        approved_indices = [int(n) - 1 for n in numbers if n.isdigit()]

        contacted = 0
        for idx in approved_indices:
            if idx < 0 or idx >= len(pending):
                continue
            agent = pending[idx]
            if agent.get("status") != "pending":
                continue

            # Contacter l'agent
            success = await _scout_contact_agent(agent)
            if success:
                agent["status"] = "contacted"
                scout.setdefault("contacted", []).append(agent["id"])
                contacted += 1
            else:
                agent["status"] = "failed"

        # Retirer les traites de la liste pending
        remaining = [p for p in pending if p.get("status") == "pending"]
        _save_pending_contacts(remaining)
        _save_scout_data(scout)

        if contacted:
            await send_mail(
                f"[MAXIA CEO] Scout: {contacted} agents contactes",
                f"{contacted} agents contactes avec succes suite a ton GO.\n\n"
                + "\n".join(
                    f"- {pending[i]['name']} ({pending[i]['registry']})"
                    for i in approved_indices if 0 <= i < len(pending)
                ),
            )
            log.info("[SCOUT] %d agents contactes apres validation Alexis", contacted)


async def _scout_contact_agent(agent: dict) -> bool:
    """Envoie le message d'invitation a un agent via son API."""
    url = agent.get("url", "")
    message = agent.get("contact_message", "")
    name = agent.get("name", "?")
    if not url or not message:
        log.warning("[SCOUT] Pas d'URL ou message pour %s", name)
        return False

    # Discovery-only: pas de contact API possible
    if agent.get("tier") == "discovery":
        log.info("[SCOUT] %s est discovery-only, contact manuel requis: %s", name, url)
        return False

    # Endpoints specifiques selon le registry
    registry = agent.get("registry", "")
    endpoints = _build_contact_endpoints(registry, url, agent)

    if not endpoints:
        log.warning("[SCOUT] Pas d'endpoint contact pour %s (%s)", name, url)
        return False

    payload = {
        "jsonrpc": "2.0",
        "method": "agent/discover",
        "params": {
            "from": "MAXIA",
            "from_url": "https://maxiaworld.app",
            "type": "marketplace_invitation",
            "message": message,
            "register_url": "https://maxiaworld.app/api/public/register",
            "mcp_manifest": "https://maxiaworld.app/mcp/manifest",
        },
        "id": 1,
    }

    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for ep in endpoints:
            try:
                resp = await client.post(ep, json=payload)
                if resp.status_code in (200, 201, 202):
                    log.info("[SCOUT] Contact OK: %s via %s", name, ep)
                    return True
                # 307/308 redirect = pas un vrai endpoint, skip
                if resp.status_code in (307, 308):
                    continue
            except Exception:
                continue

    log.warning("[SCOUT] Contact echoue: %s (tente %d endpoints)", name, len(endpoints))
    return False


def _build_contact_endpoints(registry: str, url: str, agent: dict) -> list:
    """Build list of contact endpoints based on registry type."""
    endpoints = []

    if "Agentverse" in registry:
        # Fetch.ai: Almanac messaging + endpoints standard
        addr = agent.get("id", "")
        if addr.startswith("agent1q"):
            endpoints.append(f"https://agentverse.ai/v1beta1/agents/{addr}/messages")
        base = url.rstrip("/")
        endpoints.extend([f"{base}/api/register", f"{base}/.well-known/agent.json"])
    elif "Virtuals" in registry:
        base = url.rstrip("/")
        endpoints.extend([f"{base}/api/register", f"{base}/.well-known/agent.json"])
    elif "Smithery" in registry:
        # MCP servers: essayer .well-known/agent.json sur le homepage
        homepage = url.rstrip("/")
        if homepage.startswith("http"):
            endpoints.extend([
                f"{homepage}/.well-known/agent.json",
                f"{homepage}/api/register",
            ])
    else:
        # Generic A2A protocol
        if url.startswith("http"):
            base = url.rstrip("/")
            endpoints.extend([
                f"{base}/.well-known/agent.json",
                f"{base}/api/register",
                f"{base}/api/v1/agents",
            ])

    return endpoints
