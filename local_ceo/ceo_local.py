"""CEO Local — Boucle OODA autonome sur le PC (cerveau + Playwright).

Tourne 24/7, pilote le VPS via les endpoints CEO securises.
Le LLM local (Ollama) fait le gros du travail, les API payantes sont reserves aux decisions critiques.

Usage:
    python ceo_local.py
"""
import asyncio
import json
import time
import sys
import os
import uuid
import httpx

from config_local import (
    VPS_URL, CEO_API_KEY, OODA_INTERVAL_S,
    OLLAMA_URL, OLLAMA_MODEL,
    MISTRAL_API_KEY, MISTRAL_MODEL,
    AUTO_EXECUTE_MAX_USD,
)
from audit_local import audit
from notifier import notify_all, request_approval, get_pending_approvals
from browser_agent import browser


# ══════════════════════════════════════════
# LLM Router local (simplifie — Ollama + Mistral fallback)
# ══════════════════════════════════════════

async def call_ollama(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Ollama local (0 cout)."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": full,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        print(f"[Local LLM] Ollama error: {e}")
        return ""


async def call_mistral(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Mistral API (fallback si Ollama down)."""
    if not MISTRAL_API_KEY:
        return ""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": MISTRAL_MODEL, "messages": msgs, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            return choices[0]["message"]["content"].strip() if choices else ""
    except Exception as e:
        print(f"[Local LLM] Mistral error: {e}")
        return ""


async def call_local_llm(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Ollama avec fallback Mistral."""
    result = await call_ollama(prompt, system, max_tokens)
    if result:
        return result
    return await call_mistral(prompt, system, max_tokens)


def parse_json(text: str) -> dict:
    """Parse JSON tolerant."""
    if not text:
        return {}
    try:
        c = text.strip()
        for p in ["```json", "```"]:
            if c.startswith(p):
                c = c[len(p):]
        if c.endswith("```"):
            c = c[:-3]
        return json.loads(c.strip())
    except json.JSONDecodeError:
        try:
            return json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return {}


# ══════════════════════════════════════════
# VPS API Client
# ══════════════════════════════════════════

class VPSClient:
    """Communique avec le VPS via les endpoints CEO securises."""

    def __init__(self):
        self._base = VPS_URL.rstrip("/")
        self._headers = {"X-CEO-Key": CEO_API_KEY, "Content-Type": "application/json"}

    async def get_state(self) -> dict:
        """GET /api/ceo/state — Etat complet du VPS."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{self._base}/api/ceo/state", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            print(f"[VPS] get_state error: {e}")
            return {}

    async def execute(self, action: str, agent: str, params: dict,
                      priority: str = "vert") -> dict:
        """POST /api/ceo/execute — Executer une action sur le VPS."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/execute",
                    headers=self._headers,
                    json={
                        "action": action,
                        "agent": agent,
                        "params": params,
                        "priority": priority,
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            print(f"[VPS] execute error: {e}")
            return {"success": False, "error": str(e)}

    async def health(self) -> dict:
        """GET /api/ceo/health — Sante du VPS."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base}/api/ceo/health", headers=self._headers)
                return resp.json()
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def emergency_stop(self) -> dict:
        """POST /api/ceo/emergency-stop."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self._base}/api/ceo/emergency-stop", headers=self._headers)
                return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}


# ══════════════════════════════════════════
# Boucle OODA principale
# ══════════════════════════════════════════

CEO_SYSTEM = """Tu es le CEO autonome de MAXIA, marketplace IA sur Solana.
Tu analyses l'etat du VPS et decides des actions a executer.
Sois pragmatique, frugal, et concentre sur le revenu.

ACTIONS DISPONIBLES:
- update_price: modifier un prix (params: service_id, new_price, reason)
- post_tweet: poster sur X (params: text, media?)
- post_reddit: poster sur Reddit (params: subreddit, title, body)
- send_alert: alerte Discord (params: message)
- contact_prospect: contacter un wallet (params: wallet, message, canal)
- toggle_agent: activer/desactiver un agent (params: agent_name, enabled)
- browse_competitor: screenshot concurrent (params: url)
- generate_report: generer un rapport (params: topic)

PRIORITES:
- vert: auto-execute
- orange: notification + attente validation 30 min
- rouge: notification + attente validation 2h, NE PAS auto-executer

Reponds en JSON: {
  "analysis": "analyse en 2 phrases",
  "decisions": [{"action": str, "agent": str, "params": dict, "priority": "vert|orange|rouge"}],
  "next_focus": "sur quoi se concentrer au prochain cycle"
}"""


class CEOLocal:
    """Agent CEO local avec boucle OODA."""

    def __init__(self):
        self.vps = VPSClient()
        self._running = False
        self._cycle = 0
        self._browser_ready = False
        self._daily_actions = {"date": "", "count": 0}
        print("[CEO Local] Initialise")
        print(f"  VPS: {VPS_URL}")
        print(f"  Ollama: {OLLAMA_URL}/{OLLAMA_MODEL}")
        print(f"  Intervalle: {OODA_INTERVAL_S}s")

    async def run(self):
        """Boucle OODA principale."""
        self._running = True
        print("[CEO Local] Demarre la boucle OODA")
        await notify_all("CEO Local demarre", "Boucle OODA active", "vert")

        while self._running:
            self._cycle += 1
            start = time.time()
            print(f"\n[CEO Local] === Cycle #{self._cycle} ===")

            try:
                # 1. OBSERVE — recuperer l'etat du VPS
                state = await self._observe()
                if not state:
                    print("[CEO Local] VPS inaccessible, retry dans 60s")
                    await asyncio.sleep(60)
                    continue

                # 2. ORIENT — analyser localement (0 cout)
                analysis = await self._orient(state)

                # 3. DECIDE — determiner les actions
                decisions = await self._decide(analysis, state)

                # 4. ACT — executer les actions
                await self._act(decisions)

                # 5. LOG
                elapsed = time.time() - start
                print(f"[CEO Local] Cycle #{self._cycle} complete en {elapsed:.1f}s")

            except Exception as e:
                print(f"[CEO Local] Erreur cycle #{self._cycle}: {e}")
                await audit.log(f"cycle_error: {e}", success=False)

            # 6. SLEEP
            await asyncio.sleep(OODA_INTERVAL_S)

    def stop(self):
        self._running = False

    async def _observe(self) -> dict:
        """OBSERVE — Recupere l'etat du VPS."""
        print("[OBSERVE] Recuperation etat VPS...")
        state = await self.vps.get_state()
        if state:
            kpis = state.get("kpi", {})
            print(f"  Rev 24h: ${kpis.get('revenue_24h', 0)}")
            print(f"  Clients: {kpis.get('clients_actifs', 0)}")
            print(f"  Services: {kpis.get('services_actifs', 0)}")
        return state

    async def _orient(self, state: dict) -> str:
        """ORIENT — Analyse locale via Ollama (0 cout)."""
        print("[ORIENT] Analyse locale...")
        kpis = state.get("kpi", {})
        agents = state.get("agents", {})
        errors = state.get("errors", [])

        summary = (
            f"Etat VPS MAXIA:\n"
            f"- Revenu 24h: ${kpis.get('revenue_24h', 0)}\n"
            f"- Clients actifs: {kpis.get('clients_actifs', 0)}\n"
            f"- Services actifs: {kpis.get('services_actifs', 0)}\n"
            f"- Emergency stop: {kpis.get('emergency_stop', False)}\n"
            f"- Agents: {json.dumps(agents, default=str)[:500]}\n"
            f"- Erreurs recentes: {json.dumps(errors, default=str)[:300]}\n"
        )

        analysis = await call_local_llm(
            summary + "\n\nResume la situation en 3 points cles et identifie le probleme principal.",
            system="Tu es un analyste business. Sois concis et factuel.",
            max_tokens=300,
        )
        print(f"  Analyse: {analysis[:150]}")
        return analysis

    async def _decide(self, analysis: str, state: dict) -> list:
        """DECIDE — Determiner les actions via LLM."""
        print("[DECIDE] Generation decisions...")
        kpis = state.get("kpi", {})

        prompt = (
            f"ANALYSE: {analysis}\n\n"
            f"ETAT: Rev=${kpis.get('revenue_24h', 0)}, "
            f"Clients={kpis.get('clients_actifs', 0)}, "
            f"Emergency={kpis.get('emergency_stop', False)}\n\n"
            "Quelles actions executer ? Max 3 actions par cycle.\n"
            "Privilegie les actions VERT (auto) sauf si enjeu financier > $5."
        )

        result_text = await call_local_llm(prompt, system=CEO_SYSTEM, max_tokens=800)
        result = parse_json(result_text)
        decisions = result.get("decisions", [])

        if decisions:
            print(f"  {len(decisions)} decisions generees")
            for d in decisions:
                print(f"    [{d.get('priority', '?')}] {d.get('action', '?')} -> {d.get('agent', '?')}")
        else:
            print("  Aucune decision")

        return decisions

    async def _act(self, decisions: list):
        """ACT — Executer les decisions avec gates d'approbation."""
        self._reset_daily_counter()

        from config_local import MAX_ACTIONS_DAY
        for decision in decisions:
            if self._daily_actions["count"] >= MAX_ACTIONS_DAY:
                print(f"[ACT] Limite quotidienne atteinte ({MAX_ACTIONS_DAY})")
                break

            action = decision.get("action", "")
            agent = decision.get("agent", "")
            params = decision.get("params", {})
            priority = decision.get("priority", "vert").lower()
            action_id = f"ceo_{uuid.uuid4().hex[:8]}"

            print(f"[ACT] {action} -> {agent} [{priority}]")

            # Gate d'approbation
            if priority in ("orange", "rouge"):
                approved_by = await request_approval(action_id, decision)
                if approved_by == "denied":
                    print(f"  REFUSE par {approved_by}")
                    await audit.log(action, agent, priority=priority, approved_by="denied", success=False)
                    continue
                print(f"  Approuve par: {approved_by}")
            else:
                approved_by = "auto"

            # Execution selon le type d'action
            try:
                result = await self._execute_action(action, agent, params, priority)
                success = result.get("success", False)
                detail = result.get("detail", result.get("result", ""))
                print(f"  Resultat: {'OK' if success else 'ECHEC'} — {str(detail)[:100]}")

                await audit.log(
                    action, agent, priority=priority,
                    approved_by=approved_by,
                    result=str(detail)[:500],
                    success=success,
                    vps_response=json.dumps(result, default=str)[:500],
                )
                self._daily_actions["count"] += 1

            except Exception as e:
                print(f"  ERREUR: {e}")
                await audit.log(action, agent, priority=priority, result=str(e), success=False)

    async def _execute_action(self, action: str, agent: str, params: dict,
                              priority: str) -> dict:
        """Execute une action : VPS ou Playwright local."""
        # Actions browser (locales)
        if action == "post_tweet":
            return await self._browser_tweet(params)
        elif action == "post_reddit":
            return await self._browser_reddit(params)
        elif action == "browse_competitor":
            return await self._browser_screenshot(params)
        # Actions VPS
        else:
            return await self.vps.execute(action, agent, params, priority)

    async def _browser_tweet(self, params: dict) -> dict:
        """Post tweet via Playwright, fallback VPS."""
        text = params.get("text", "")
        if not text:
            return {"success": False, "detail": "No tweet text"}
        try:
            result = await browser.post_tweet(text, params.get("media"))
            if result.get("success"):
                return {"success": True, "detail": f"Tweet posted via browser: {text[:60]}"}
            # Fallback VPS (tweepy)
            print("[ACT] Browser tweet failed, fallback VPS...")
            return await self.vps.execute("post_tweet", "GHOST-WRITER", params, "vert")
        except Exception as e:
            print(f"[ACT] Browser tweet error: {e}, fallback VPS")
            return await self.vps.execute("post_tweet", "GHOST-WRITER", params, "vert")

    async def _browser_reddit(self, params: dict) -> dict:
        """Post Reddit via Playwright."""
        subreddit = params.get("subreddit", "")
        title = params.get("title", "")
        body = params.get("body", "")
        if not subreddit or not title:
            return {"success": False, "detail": "Missing subreddit or title"}
        try:
            return await browser.post_reddit(subreddit, title, body)
        except Exception as e:
            return {"success": False, "detail": str(e)}

    async def _browser_screenshot(self, params: dict) -> dict:
        """Screenshot concurrent via Playwright."""
        url = params.get("url", "")
        if not url:
            return {"success": False, "detail": "No URL"}
        try:
            path = await browser.screenshot_page(url)
            return {"success": bool(path), "detail": f"Screenshot saved: {path}"}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def _reset_daily_counter(self):
        today = time.strftime("%Y-%m-%d")
        if self._daily_actions["date"] != today:
            self._daily_actions = {"date": today, "count": 0}


# ══════════════════════════════════════════
# Main
# ══════════════════════════════════════════

async def main():
    if not CEO_API_KEY:
        print("ERREUR: CEO_API_KEY non configure dans .env")
        sys.exit(1)

    ceo = CEOLocal()

    # Verifier la connexion VPS
    health = await ceo.vps.health()
    if health.get("healthy"):
        print("[CEO Local] VPS connecte et en bonne sante")
    else:
        print(f"[CEO Local] VPS indisponible: {health}")
        print("  Demarrage quand meme (retry automatique)")

    # Verifier Ollama
    try:
        test = await call_ollama("Dis 'ok' en un mot.", max_tokens=10)
        print(f"[CEO Local] Ollama OK: {test.strip()}")
    except Exception as e:
        print(f"[CEO Local] Ollama indisponible: {e}")
        print("  Fallback Mistral sera utilise")

    try:
        await ceo.run()
    except KeyboardInterrupt:
        print("\n[CEO Local] Arret demande")
        ceo.stop()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
