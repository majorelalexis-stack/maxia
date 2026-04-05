"""MAXIA AgentWorker V12 — Cerebras gpt-oss-120b Multilingue"""
import logging
import asyncio, json, os, time, hashlib

logger = logging.getLogger(__name__)
from core.config import CEREBRAS_API_KEY, CEREBRAS_MODEL, AGENT_TIMEOUT_S
from core.error_utils import safe_error
from core.http_client import get_http_client

_cerebras_ready = bool(CEREBRAS_API_KEY)
if _cerebras_ready:
    logger.info(f"Cerebras active ({CEREBRAS_MODEL}) — multilingue")
else:
    logger.warning("CEREBRAS_API_KEY manquant")

try:
    from core.database import db
except Exception:
    db = None

# Prompts systeme multilingues — l'agent repond dans la langue du prompt
SERVICE_PROMPTS = {
    "text":      "You are MAXIA Text Agent. Respond professionally in the SAME LANGUAGE as the user prompt. If the user writes in French, respond in French. If in English, respond in English. Auto-detect the language.",
    "code":      "You are MAXIA Code Agent. Provide clean, commented, secure code. Respond in the SAME LANGUAGE as the user prompt for comments and explanations.",
    "data":      "You are MAXIA Data Agent. Analyze crypto/DeFi data. Respond in the SAME LANGUAGE as the user prompt.",
    "audit":     "You are MAXIA Security Agent. Structure: [CRITICAL][MAJOR][MINOR][INFO]. Respond in the SAME LANGUAGE as the user prompt.",
    "image_gen": "You are MAXIA Creative Agent. Provide an optimized prompt for SD/DALLE. Always respond in English for image prompts.",
    "default":   "You are MAXIA AI Agent. Respond professionally in the SAME LANGUAGE as the user prompt. Auto-detect the language.",
}


class AgentWorker:
    def __init__(self):
        self._active: set = set()
        self._external: dict = {}
        self._broadcast_fn = None

    def set_broadcast(self, fn):
        self._broadcast_fn = fn

    def register_external_agent(self, wallet: str):
        self._external[wallet] = time.time()
        # P1 fix: cleanup stale entries (>24h) to prevent unbounded growth
        if len(self._external) > 1000:
            cutoff = time.time() - 86400
            self._external = {k: v for k, v in self._external.items() if v > cutoff}

    async def run(self):
        logger.info(f"Demarre (timeout={AGENT_TIMEOUT_S}s, multilingue)")
        while True:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Erreur: {e}")
            await asyncio.sleep(2)

    async def _tick(self):
        if db is None:
            return
        try:
            rows = await db.raw_execute_fetchall(
                "SELECT data FROM commands WHERE json_extract(data,'$.status')='pending'")
        except Exception:
            return
        for row in rows:
            cmd = json.loads(row["data"] if isinstance(row, dict) else row[0])
            cid = cmd["commandId"]
            if cid in self._active:
                continue
            age = time.time() - cmd.get("createdAt", time.time())
            if age < AGENT_TIMEOUT_S:
                continue
            cutoff = time.time() - 30
            if any(t > cutoff for t in self._external.values()):
                continue
            self._active.add(cid)
            asyncio.create_task(self._handle(cmd))

    async def _handle(self, cmd: dict):
        cid = cmd["commandId"]
        try:
            stype = self._detect(cmd.get("serviceId", ""))
            result = await self._call_llm(stype, cmd.get("prompt", ""), cmd)
            rhash = hashlib.sha256(result.encode()).hexdigest()
            update = {
                "status": "completed", "result": result, "resultHash": rhash,
                "agent": f"cerebras/{CEREBRAS_MODEL}", "completedAt": int(time.time()),
            }
            await self._save(cid, update)
            if self._broadcast_fn:
                await self._broadcast_fn({
                    "type": "COMMAND_COMPLETED", "commandId": cid,
                    "buyer": cmd.get("buyerWallet", ""),
                    "agent": f"cerebras/{CEREBRAS_MODEL}", "result": result,
                })
        except Exception as e:
            logger.error(f"{cid[:8]}...: {e}")
            err = safe_error(e, "agent_worker_execute")
            await self._save(cid, {"status": "failed", "error": err["error"], "request_id": err["request_id"], "completedAt": int(time.time())})
        finally:
            self._active.discard(cid)

    async def _call_llm(self, stype: str, prompt: str, cmd: dict) -> str:
        if not _cerebras_ready:
            raise RuntimeError("Cerebras API non disponible")
        system = SERVICE_PROMPTS.get(stype, SERVICE_PROMPTS["default"])
        user_msg = f"[Service: {cmd.get('serviceId', 'N/A')}]\n[Buyer: {cmd.get('buyerWallet', '')[:8]}...]\n\n{prompt}"
        client = get_http_client()
        resp = await client.post(
            "https://api.cerebras.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {CEREBRAS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": CEREBRAS_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 4096,
                "temperature": 0.7,
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"].strip() if choices else ""

    def _detect(self, sid: str) -> str:
        s = sid.lower()
        if any(k in s for k in ("image", "img", "art", "creative")):
            return "image_gen"
        if any(k in s for k in ("code", "dev", "sentinel")):
            return "code"
        if any(k in s for k in ("data", "market", "predict")):
            return "data"
        if any(k in s for k in ("audit", "secu", "vuln")):
            return "audit"
        return "text"

    async def _save(self, cid: str, update: dict):
        if db is None:
            return
        try:
            rows = await db.raw_execute_fetchall("SELECT data FROM commands WHERE command_id=?", (cid,))
            row = rows[0] if rows else None
            if not row:
                return
            d = json.loads(row[0] if not isinstance(row, dict) else row["data"])
            d.update(update)
            await db.raw_execute("UPDATE commands SET data=? WHERE command_id=?", (json.dumps(d), cid))
        except Exception as e:
            logger.error(f"DB error: {e}")


agent_worker = AgentWorker()
