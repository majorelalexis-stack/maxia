"""MAXIA Audit-as-a-Service — AI-powered code/contract/wallet audits ($4.99).

Uses LLM router (Cerebras -> Gemini -> Groq -> Mistral -> Claude fallback).
Results cached 24h by input hash to avoid double-charging.
"""
import hashlib, json, logging, time, uuid
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("maxia.audit_service")

router = APIRouter(prefix="/api/services", tags=["audit-service"])

# ── Config ──
AUDIT_PRICE_USDC = 4.99
try:
    from core.config import SERVICE_PRICES
    AUDIT_PRICE_USDC = SERVICE_PRICES.get("maxia-audit", 4.99)
except Exception:
    pass

CACHE_TTL_SECONDS = 86400  # 24h

# ── DB table ──
AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audits (
    audit_id TEXT PRIMARY KEY,
    api_key TEXT,
    audit_type TEXT,
    input_hash TEXT,
    result_json TEXT,
    risk_score INTEGER,
    created_at INTEGER,
    price_usdc REAL DEFAULT 4.99
);
"""

# ── Pydantic models ──

class AuditRequest(BaseModel):
    audit_type: str = Field(
        ..., description="smart_contract | token_analysis | wallet_security",
    )
    code: Optional[str] = Field(None, description="Source code for smart_contract audit")
    contract_address: Optional[str] = Field(None, description="Contract/token address")
    chain: str = Field("solana", description="Target chain")


class AuditFinding(BaseModel):
    severity: str
    title: str
    description: str
    location: Optional[str] = None


class AuditResult(BaseModel):
    audit_id: str
    type: str
    risk_score: int
    risk_level: str
    findings: List[AuditFinding]
    summary: str
    recommendations: List[str]
    audited_at: int
    price_usdc: float = AUDIT_PRICE_USDC
    cached: bool = False


VALID_AUDIT_TYPES = {"smart_contract", "token_analysis", "wallet_security"}
VALID_CHAINS = {
    "solana", "base", "ethereum", "polygon", "arbitrum", "avalanche",
    "bnb", "ton", "sui", "tron", "near", "aptos", "sei", "bitcoin", "xrp",
}


# ── Helpers ──

def _compute_input_hash(req: AuditRequest) -> str:
    """Deterministic hash for cache dedup."""
    raw = f"{req.audit_type}:{req.chain}:{req.code or ''}:{req.contract_address or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _risk_level(score: int) -> str:
    if score <= 20:
        return "LOW"
    if score <= 50:
        return "MEDIUM"
    if score <= 75:
        return "HIGH"
    return "CRITICAL"


async def _ensure_table() -> None:
    """Create audits table if missing."""
    try:
        from core.database import db
        await db.raw_executescript(AUDIT_TABLE_SQL)
    except Exception as e:
        logger.warning("Failed to create audits table: %s", e)


async def _find_cached(input_hash: str) -> Optional[dict]:
    """Return cached audit if within TTL."""
    try:
        from core.database import db
        cutoff = int(time.time()) - CACHE_TTL_SECONDS
        rows = await db.raw_execute_fetchall(
            "SELECT audit_id, result_json, risk_score, created_at "
            "FROM audits WHERE input_hash = ? AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 1",
            (input_hash, cutoff),
        )
        if rows:
            row = rows[0]
            result = json.loads(row["result_json"])
            result["cached"] = True
            return result
    except Exception as e:
        logger.warning("Cache lookup failed: %s", e)
    return None


async def _store_audit(
    audit_id: str, api_key: str, audit_type: str,
    input_hash: str, result: dict, risk_score: int,
) -> None:
    """Persist audit result to DB."""
    try:
        from core.database import db
        await db.raw_execute(
            "INSERT INTO audits (audit_id, api_key, audit_type, input_hash, "
            "result_json, risk_score, created_at, price_usdc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id, api_key, audit_type, input_hash,
                json.dumps(result), risk_score, int(time.time()), AUDIT_PRICE_USDC,
            ),
        )
    except Exception as e:
        logger.error("Failed to store audit %s: %s", audit_id, e)


# ── LLM prompts ──

_SYSTEM_PROMPT = (
    "You are MAXIA Audit, an expert security auditor for smart contracts, "
    "tokens, and wallets across 15 blockchains. You produce structured JSON "
    "audit reports. Be thorough, specific, and honest. "
    "If you cannot determine something, say so explicitly."
)

_JSON_INSTRUCTION = (
    '\nRespond ONLY with valid JSON (no markdown, no extra text):\n'
    '{"risk_score": <0-100>, "findings": [{"severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO", '
    '"title": "...", "description": "...", "location": "..."}], '
    '"summary": "1-3 sentences", "recommendations": ["..."]}\n'
)


def _build_smart_contract_prompt(code: str, chain: str) -> str:
    return (
        f"Audit the following smart contract code for security vulnerabilities.\n"
        f"Chain: {chain}\n\n"
        "Analyze for: reentrancy, integer overflow/underflow, access control issues, "
        "unchecked external calls, front-running, logic errors, gas optimization, "
        "missing events, delegatecall misuse, storage collisions. "
        "For Solana/Rust: missing signer checks, PDA seed collisions, account validation.\n\n"
        f"Source code:\n```\n{code[:8000]}\n```\n{_JSON_INSTRUCTION}"
    )


def _build_token_analysis_prompt(contract_address: str, chain: str) -> str:
    return (
        f"Analyze token contract for rug pull indicators.\n"
        f"Chain: {chain}\nContract: {contract_address}\n\n"
        "Evaluate: mint authority active, freeze authority, blacklist/whitelist, "
        "pause trading capability, hidden fees/tax, liquidity lock status, "
        "top holder concentration, contract verification, honeypot indicators, "
        "proxy/upgrade patterns.\n"
        "If you cannot verify on-chain data, state that clearly.\n"
        f"{_JSON_INSTRUCTION}"
    )


def _build_wallet_security_prompt(contract_address: str, chain: str) -> str:
    return (
        f"Audit wallet security practices.\n"
        f"Chain: {chain}\nWallet: {contract_address}\n\n"
        "Evaluate: unlimited token approvals, interaction with scam contracts, "
        "governance delegation risks, DeFi liquidation proximity, NFT approval risks, "
        "permission exposure, phishing signing patterns, revocation recommendations.\n"
        "If you cannot verify on-chain data, state that clearly.\n"
        f"{_JSON_INSTRUCTION}"
    )


def _parse_llm_response(raw: str, audit_id: str, audit_type: str) -> dict:
    """Parse LLM JSON response into structured audit result."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(l for l in cleaned.split("\n") if not l.strip().startswith("```"))

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                return _partial_result(audit_id, audit_type, raw)
        else:
            return _partial_result(audit_id, audit_type, raw)

    risk_score = max(0, min(100, int(data.get("risk_score", 50))))
    findings_raw = data.get("findings", [])
    findings = []
    for f in findings_raw:
        if isinstance(f, dict):
            findings.append({
                "severity": str(f.get("severity", "INFO")).upper(),
                "title": str(f.get("title", "Unnamed finding")),
                "description": str(f.get("description", "")),
                "location": f.get("location"),
            })

    return {
        "audit_id": audit_id,
        "type": audit_type,
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score),
        "findings": findings,
        "summary": str(data.get("summary", "Audit completed.")),
        "recommendations": [str(r) for r in data.get("recommendations", [])],
        "audited_at": int(time.time()),
        "price_usdc": AUDIT_PRICE_USDC,
        "cached": False,
    }


def _partial_result(audit_id: str, audit_type: str, raw_text: str) -> dict:
    """Fallback when LLM response is not valid JSON."""
    return {
        "audit_id": audit_id,
        "type": audit_type,
        "risk_score": 50,
        "risk_level": "MEDIUM",
        "findings": [{
            "severity": "INFO",
            "title": "Partial analysis",
            "description": raw_text[:2000],
            "location": None,
        }],
        "summary": "The AI auditor returned a non-structured response. "
                   "Review the finding description for the raw analysis.",
        "recommendations": [
            "Request a new audit if the result is insufficient",
            "Consider a manual audit for critical contracts",
        ],
        "audited_at": int(time.time()),
        "price_usdc": AUDIT_PRICE_USDC,
        "cached": False,
    }


# ── Endpoints ──

@router.post("/audit", response_model=AuditResult)
async def submit_audit(
    req: AuditRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict:
    """Submit code/contract/wallet for AI-powered security audit ($4.99)."""
    await _ensure_table()

    if req.audit_type not in VALID_AUDIT_TYPES:
        raise HTTPException(400, f"Invalid audit_type. Must be: {', '.join(sorted(VALID_AUDIT_TYPES))}")
    if req.chain.lower() not in VALID_CHAINS:
        raise HTTPException(400, f"Unsupported chain '{req.chain}'")
    if req.audit_type == "smart_contract" and not req.code:
        raise HTTPException(400, "'code' required for smart_contract audit")
    if req.audit_type in ("token_analysis", "wallet_security") and not req.contract_address:
        raise HTTPException(400, "'contract_address' required for token_analysis/wallet_security")

    chain = req.chain.lower()
    input_hash = _compute_input_hash(req)

    # Check cache (same input within 24h = free, no double charge)
    cached = await _find_cached(input_hash)
    if cached:
        logger.info("Audit cache hit for hash %s", input_hash[:8])
        return cached

    prompt_builders = {
        "smart_contract": lambda: _build_smart_contract_prompt(req.code, chain),
        "token_analysis": lambda: _build_token_analysis_prompt(req.contract_address, chain),
        "wallet_security": lambda: _build_wallet_security_prompt(req.contract_address, chain),
    }
    prompt = prompt_builders[req.audit_type]()

    audit_id = f"aud-{uuid.uuid4().hex[:12]}"
    try:
        from ai.llm_router import router as llm_router, Tier
        raw_response = await llm_router.call(
            prompt=prompt, tier=Tier.FAST, system=_SYSTEM_PROMPT,
            max_tokens=1500, timeout=45.0,
        )
    except Exception as e:
        logger.error("LLM call failed for audit %s: %s", audit_id, e)
        raise HTTPException(503, "Audit service temporarily unavailable. Please retry.")

    if not raw_response:
        raise HTTPException(503, "LLM returned empty response. Please retry.")

    # Parse response
    result = _parse_llm_response(raw_response, audit_id, req.audit_type)

    # Store in DB
    await _store_audit(
        audit_id=audit_id,
        api_key=x_api_key,
        audit_type=req.audit_type,
        input_hash=input_hash,
        result=result,
        risk_score=result["risk_score"],
    )

    logger.info(
        "Audit %s completed: type=%s chain=%s risk=%d",
        audit_id, req.audit_type, chain, result["risk_score"],
    )
    return result


@router.get("/audit/history")
async def list_audits(
    x_api_key: str = Header(..., alias="X-API-Key"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """List past audits for the caller's API key."""
    await _ensure_table()
    try:
        from core.database import db
        rows = await db.raw_execute_fetchall(
            "SELECT audit_id, audit_type, risk_score, created_at, price_usdc "
            "FROM audits WHERE api_key = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (x_api_key, limit, offset),
        )
        count_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as total FROM audits WHERE api_key = ?",
            (x_api_key,),
        )
    except Exception as e:
        logger.error("DB error listing audits: %s", e)
        raise HTTPException(status_code=500, detail="Database error")

    total = count_rows[0]["total"] if count_rows else 0
    audits = [{
        "audit_id": r["audit_id"],
        "audit_type": r["audit_type"],
        "risk_score": r["risk_score"],
        "risk_level": _risk_level(r["risk_score"]),
        "created_at": r["created_at"],
        "price_usdc": r["price_usdc"],
    } for r in rows]

    return {"audits": audits, "total": total, "limit": limit, "offset": offset}


@router.get("/audit/{audit_id}")
async def get_audit(
    audit_id: str,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict:
    """Retrieve a specific audit result by ID."""
    await _ensure_table()
    try:
        from core.database import db
        rows = await db.raw_execute_fetchall(
            "SELECT result_json, api_key FROM audits WHERE audit_id = ?",
            (audit_id,),
        )
    except Exception as e:
        logger.error("DB error fetching audit %s: %s", audit_id, e)
        raise HTTPException(status_code=500, detail="Database error")

    if not rows:
        raise HTTPException(status_code=404, detail="Audit not found")

    row = rows[0]
    if row["api_key"] != x_api_key:
        raise HTTPException(status_code=403, detail="Access denied")

    result = json.loads(row["result_json"])
    result["cached"] = False
    return result
