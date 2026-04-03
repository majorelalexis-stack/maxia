"""MAXIA Art.49 — Composable Agent Builder

Lets users assemble AI agents from pre-built modular components without
writing code.  Each agent template chains: trigger -> filter -> processor -> action.

Components:
  - trigger:   schedule, price_alert, webhook, on_trade, on_message
  - processor: llm_analyze, sentiment, summarize, translate, custom_prompt
  - action:    swap_token, send_alert, post_twitter, execute_service,
               send_telegram, log_result
  - filter:    price_threshold, volume_filter, whitelist_tokens,
               time_window, confidence_gate

Auth: X-API-Key header (validated against agents table).
"""
import logging
import json
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field

from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-builder", tags=["agent-builder"])

# ── Schema (run once via ensure_schema) ──

AGENT_BUILDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    components TEXT NOT NULL DEFAULT '[]',
    config TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at INTEGER DEFAULT (strftime('%s','now')),
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_at_owner ON agent_templates(owner_wallet, status);
"""

VALID_STATUSES = frozenset({"draft", "active", "paused", "error"})

# ── Component Registry ──

# Each component: id, type, name, description, config_schema, version
# config_schema is a flat dict of {param: {"type": ..., "required": bool, "description": ...}}

COMPONENT_REGISTRY: list[dict[str, Any]] = [
    # ── Triggers ──
    {
        "id": "trigger.schedule",
        "type": "trigger",
        "name": "Schedule (Cron)",
        "description": "Declenche l'agent selon un cron schedule (ex: toutes les 5 min).",
        "config_schema": {
            "cron_expr": {"type": "string", "required": True, "description": "Cron expression (e.g. '*/5 * * * *')"},
            "timezone": {"type": "string", "required": False, "description": "IANA timezone (default UTC)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "trigger.price_alert",
        "type": "trigger",
        "name": "Price Alert",
        "description": "Declenche quand le prix d'un token depasse un seuil.",
        "config_schema": {
            "token": {"type": "string", "required": True, "description": "Token symbol (e.g. SOL, ETH)"},
            "direction": {"type": "string", "required": True, "description": "'above' or 'below'"},
            "threshold_usd": {"type": "number", "required": True, "description": "Price threshold in USD"},
        },
        "version": "1.0.0",
    },
    {
        "id": "trigger.webhook",
        "type": "trigger",
        "name": "Webhook",
        "description": "Declenche via un appel HTTP POST entrant.",
        "config_schema": {
            "secret": {"type": "string", "required": False, "description": "HMAC secret for webhook validation"},
        },
        "version": "1.0.0",
    },
    {
        "id": "trigger.on_trade",
        "type": "trigger",
        "name": "On Trade",
        "description": "Declenche apres chaque trade execute sur le marketplace.",
        "config_schema": {
            "token_filter": {"type": "string", "required": False, "description": "Filter by token symbol (optional)"},
            "min_amount_usd": {"type": "number", "required": False, "description": "Minimum trade amount in USD"},
        },
        "version": "1.0.0",
    },
    {
        "id": "trigger.on_message",
        "type": "trigger",
        "name": "On Message",
        "description": "Declenche quand un message arrive (WebSocket, Telegram, etc.).",
        "config_schema": {
            "source": {"type": "string", "required": True, "description": "'websocket', 'telegram', or 'discord'"},
            "keyword_filter": {"type": "string", "required": False, "description": "Only trigger if message contains keyword"},
        },
        "version": "1.0.0",
    },
    # ── Processors ──
    {
        "id": "processor.llm_analyze",
        "type": "processor",
        "name": "LLM Analyze",
        "description": "Analyse les donnees avec un LLM (Groq/Mistral/Claude fallback).",
        "config_schema": {
            "prompt_template": {"type": "string", "required": True, "description": "Prompt template (use {data} placeholder)"},
            "max_tokens": {"type": "integer", "required": False, "description": "Max tokens for LLM response (default 500)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "processor.sentiment",
        "type": "processor",
        "name": "Sentiment Analysis",
        "description": "Analyse le sentiment (bullish/bearish/neutral) des donnees entrantes.",
        "config_schema": {
            "language": {"type": "string", "required": False, "description": "Language code (default 'en')"},
        },
        "version": "1.0.0",
    },
    {
        "id": "processor.summarize",
        "type": "processor",
        "name": "Summarize",
        "description": "Resume les donnees en quelques phrases cles.",
        "config_schema": {
            "max_sentences": {"type": "integer", "required": False, "description": "Max sentences in summary (default 3)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "processor.translate",
        "type": "processor",
        "name": "Translate",
        "description": "Traduit le texte dans une langue cible.",
        "config_schema": {
            "target_language": {"type": "string", "required": True, "description": "Target language code (e.g. 'fr', 'es')"},
        },
        "version": "1.0.0",
    },
    {
        "id": "processor.custom_prompt",
        "type": "processor",
        "name": "Custom Prompt",
        "description": "Envoie un prompt personnalise au LLM avec les donnees en contexte.",
        "config_schema": {
            "system_prompt": {"type": "string", "required": True, "description": "System prompt for the LLM"},
            "user_prompt_template": {"type": "string", "required": True, "description": "User prompt template ({data} placeholder)"},
            "temperature": {"type": "number", "required": False, "description": "LLM temperature 0.0-1.0 (default 0.3)"},
        },
        "version": "1.0.0",
    },
    # ── Actions ──
    {
        "id": "action.swap_token",
        "type": "action",
        "name": "Swap Token",
        "description": "Execute un swap de token via Jupiter (Solana) ou 0x (EVM).",
        "config_schema": {
            "from_token": {"type": "string", "required": True, "description": "Source token symbol"},
            "to_token": {"type": "string", "required": True, "description": "Destination token symbol"},
            "amount_usd": {"type": "number", "required": True, "description": "Amount in USD to swap"},
            "slippage_bps": {"type": "integer", "required": False, "description": "Slippage in basis points (default 50)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "action.send_alert",
        "type": "action",
        "name": "Send Alert",
        "description": "Envoie une alerte via Discord webhook.",
        "config_schema": {
            "webhook_url": {"type": "string", "required": True, "description": "Discord webhook URL"},
            "message_template": {"type": "string", "required": False, "description": "Message template ({result} placeholder)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "action.post_twitter",
        "type": "action",
        "name": "Post Twitter",
        "description": "Publie un tweet ou un commentaire via le bot Twitter MAXIA.",
        "config_schema": {
            "action_type": {"type": "string", "required": True, "description": "'tweet' or 'reply'"},
            "content_template": {"type": "string", "required": True, "description": "Tweet content ({result} placeholder, max 280 chars)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "action.execute_service",
        "type": "action",
        "name": "Execute Service",
        "description": "Execute un service du marketplace MAXIA par son ID.",
        "config_schema": {
            "service_id": {"type": "string", "required": True, "description": "MAXIA service ID"},
            "input_template": {"type": "string", "required": False, "description": "Input for the service ({result} placeholder)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "action.send_telegram",
        "type": "action",
        "name": "Send Telegram",
        "description": "Envoie un message Telegram via @MAXIA_AI_bot.",
        "config_schema": {
            "chat_id": {"type": "string", "required": True, "description": "Telegram chat ID"},
            "message_template": {"type": "string", "required": False, "description": "Message template ({result} placeholder)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "action.log_result",
        "type": "action",
        "name": "Log Result",
        "description": "Enregistre le resultat dans la base de donnees pour audit.",
        "config_schema": {
            "log_level": {"type": "string", "required": False, "description": "'info', 'warning', or 'error' (default 'info')"},
        },
        "version": "1.0.0",
    },
    # ── Filters ──
    {
        "id": "filter.price_threshold",
        "type": "filter",
        "name": "Price Threshold",
        "description": "Laisse passer seulement si le prix depasse un seuil.",
        "config_schema": {
            "token": {"type": "string", "required": True, "description": "Token symbol"},
            "min_usd": {"type": "number", "required": False, "description": "Minimum price (pass if above)"},
            "max_usd": {"type": "number", "required": False, "description": "Maximum price (pass if below)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "filter.volume_filter",
        "type": "filter",
        "name": "Volume Filter",
        "description": "Filtre selon le volume 24h d'un token.",
        "config_schema": {
            "token": {"type": "string", "required": True, "description": "Token symbol"},
            "min_volume_usd": {"type": "number", "required": True, "description": "Minimum 24h volume in USD"},
        },
        "version": "1.0.0",
    },
    {
        "id": "filter.whitelist_tokens",
        "type": "filter",
        "name": "Whitelist Tokens",
        "description": "Laisse passer seulement les tokens dans la liste blanche.",
        "config_schema": {
            "tokens": {"type": "array", "required": True, "description": "List of allowed token symbols (e.g. ['SOL','ETH','BTC'])"},
        },
        "version": "1.0.0",
    },
    {
        "id": "filter.time_window",
        "type": "filter",
        "name": "Time Window",
        "description": "Filtre pour n'executer que dans une fenetre horaire.",
        "config_schema": {
            "start_hour_utc": {"type": "integer", "required": True, "description": "Start hour UTC (0-23)"},
            "end_hour_utc": {"type": "integer", "required": True, "description": "End hour UTC (0-23)"},
        },
        "version": "1.0.0",
    },
    {
        "id": "filter.confidence_gate",
        "type": "filter",
        "name": "Confidence Gate",
        "description": "Bloque si le score de confiance est inferieur au seuil.",
        "config_schema": {
            "min_confidence": {"type": "number", "required": True, "description": "Minimum confidence score 0.0-1.0"},
        },
        "version": "1.0.0",
    },
]

# Index for fast lookup
_COMPONENT_INDEX: dict[str, dict] = {c["id"]: c for c in COMPONENT_REGISTRY}
_COMPONENT_TYPES: frozenset[str] = frozenset({"trigger", "processor", "action", "filter"})

# ── Auth dependency ──

async def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    """Validate X-API-Key and return agent info (wallet, api_key)."""
    if not x_api_key or len(x_api_key) < 8:
        raise HTTPException(401, "X-API-Key required")
    try:
        from core.database import db
        rows = await db.raw_execute_fetchall(
            "SELECT api_key, wallet FROM agents WHERE api_key=? LIMIT 1",
            (x_api_key,),
        )
        if not rows:
            raise HTTPException(401, "Invalid API key")
        agent = dict(rows[0]) if not isinstance(rows[0], dict) else rows[0]
        return {"api_key": agent["api_key"], "wallet": agent["wallet"]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("API key validation error: %s", exc)
        raise HTTPException(503, "Auth service unavailable")


# ── DB helpers ──

async def _ensure_schema() -> None:
    """Create the agent_templates table if it doesn't exist."""
    try:
        from core.database import db
        await db.raw_executescript(AGENT_BUILDER_SCHEMA)
    except Exception as exc:
        logger.error("agent_builder schema init failed: %s", exc)


async def _get_template(template_id: str, owner_wallet: str) -> dict:
    """Fetch a single template owned by wallet. Raises 404 if not found."""
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT id, name, owner_wallet, components, config, status, "
        "created_at, updated_at FROM agent_templates WHERE id=? AND owner_wallet=?",
        (template_id, owner_wallet),
    )
    if not rows:
        raise HTTPException(404, "Template not found")
    return _row_to_dict(rows[0])


def _row_to_dict(row: Any) -> dict:
    """Convert a DB row to a clean dict with parsed JSON fields."""
    r = dict(row) if not isinstance(row, dict) else row
    for json_field in ("components", "config"):
        val = r.get(json_field)
        if isinstance(val, str):
            try:
                r[json_field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                r[json_field] = []
    return r


# ── Validation ──

def _validate_component_ref(comp: dict) -> str | None:
    """Validate a single component reference. Returns error string or None."""
    comp_id = comp.get("id")
    if not comp_id or comp_id not in _COMPONENT_INDEX:
        return f"Unknown component: {comp_id}"

    registry_entry = _COMPONENT_INDEX[comp_id]
    comp_config = comp.get("config", {})
    schema = registry_entry["config_schema"]

    # Check required params
    for param_name, param_def in schema.items():
        if param_def.get("required") and param_name not in comp_config:
            return f"Component '{comp_id}' missing required param: {param_name}"

        # Type check provided values
        if param_name in comp_config:
            expected = param_def.get("type", "string")
            value = comp_config[param_name]
            if not _check_type(value, expected):
                return (
                    f"Component '{comp_id}' param '{param_name}': "
                    f"expected {expected}, got {type(value).__name__}"
                )

    return None


def _check_type(value: Any, expected: str) -> bool:
    """Check if a value matches the expected JSON schema type."""
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected_type = type_map.get(expected, str)
    return isinstance(value, expected_type)


def _validate_pipeline(components: list[dict]) -> list[str]:
    """Validate the full component pipeline. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    if not components:
        return ["At least one component is required"]

    type_counts: dict[str, int] = {"trigger": 0, "processor": 0, "action": 0, "filter": 0}
    seen_ids: set[str] = set()

    for i, comp in enumerate(components):
        comp_id = comp.get("id", "")

        # Check for duplicates (same component used twice is OK, but flag exact same instance)
        instance_key = f"{comp_id}:{json.dumps(comp.get('config', {}), sort_keys=True)}"
        if instance_key in seen_ids:
            errors.append(f"Duplicate component instance at index {i}: {comp_id}")
        seen_ids.add(instance_key)

        # Validate individual component
        err = _validate_component_ref(comp)
        if err:
            errors.append(err)
            continue

        registry_entry = _COMPONENT_INDEX[comp_id]
        comp_type = registry_entry["type"]
        if comp_type in type_counts:
            type_counts[comp_type] += 1

    # Pipeline constraints
    if type_counts["trigger"] < 1:
        errors.append("Pipeline must have at least 1 trigger component")
    if type_counts["action"] < 1:
        errors.append("Pipeline must have at least 1 action component")

    return errors


# ── Pydantic Models ──

class ComponentRef(BaseModel):
    id: str = Field(description="Component ID from the registry (e.g. 'trigger.schedule')")
    config: dict = Field(default_factory=dict, description="Component configuration")


class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Agent template name")
    components: list[ComponentRef] = Field(min_length=1, max_length=20)
    config: dict = Field(default_factory=dict, description="Global agent config (optional)")


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    components: Optional[list[ComponentRef]] = Field(None, min_length=1, max_length=20)
    config: Optional[dict] = None


# ── Endpoints ──

@router.on_event("startup")
async def _startup() -> None:
    await _ensure_schema()


@router.get("/components")
async def list_components(
    type_filter: Optional[str] = None,
) -> dict:
    """List all available components with their config schemas."""
    results = COMPONENT_REGISTRY
    if type_filter:
        if type_filter not in _COMPONENT_TYPES:
            raise HTTPException(
                400,
                f"Invalid type filter. Must be one of: {', '.join(sorted(_COMPONENT_TYPES))}",
            )
        results = [c for c in COMPONENT_REGISTRY if c["type"] == type_filter]

    return {
        "components": results,
        "total": len(results),
        "types": sorted(_COMPONENT_TYPES),
    }


@router.post("/create", status_code=201)
async def create_template(
    req: CreateTemplateRequest,
    agent: dict = Depends(_require_api_key),
) -> dict:
    """Create a new agent template from components."""
    # Content safety check
    try:
        from core.security import check_content_safety
        check_content_safety(req.name)
    except Exception:
        raise HTTPException(400, "Template name contains prohibited content")

    # Validate pipeline
    comp_dicts = [{"id": c.id, "config": c.config} for c in req.components]
    errors = _validate_pipeline(comp_dicts)
    if errors:
        raise HTTPException(422, {"validation_errors": errors})

    template_id = str(uuid.uuid4())
    now = int(time.time())

    try:
        from core.database import db
        await db.raw_execute(
            "INSERT INTO agent_templates(id, name, owner_wallet, components, config, status, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id,
                req.name,
                agent["wallet"],
                json.dumps(comp_dicts),
                json.dumps(req.config),
                "draft",
                now,
                now,
            ),
        )
    except Exception as exc:
        logger.error("Failed to create template: %s", exc)
        raise HTTPException(500, safe_error(exc, "create_template"))

    logger.info("Agent template created: %s by %s", template_id, agent["wallet"])
    return {
        "id": template_id,
        "name": req.name,
        "status": "draft",
        "components_count": len(comp_dicts),
        "created_at": now,
    }


@router.get("/templates")
async def list_templates(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    agent: dict = Depends(_require_api_key),
) -> dict:
    """List the caller's agent templates."""
    if limit < 1 or limit > 200:
        limit = 50
    if offset < 0:
        offset = 0

    try:
        from core.database import db
        if status:
            if status not in VALID_STATUSES:
                raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}")
            rows = await db.raw_execute_fetchall(
                "SELECT id, name, owner_wallet, components, config, status, created_at, updated_at "
                "FROM agent_templates WHERE owner_wallet=? AND status=? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (agent["wallet"], status, limit, offset),
            )
        else:
            rows = await db.raw_execute_fetchall(
                "SELECT id, name, owner_wallet, components, config, status, created_at, updated_at "
                "FROM agent_templates WHERE owner_wallet=? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (agent["wallet"], limit, offset),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, safe_error(exc, "list_templates"))

    templates = [_row_to_dict(r) for r in rows]
    return {"templates": templates, "count": len(templates), "limit": limit, "offset": offset}


@router.get("/templates/{template_id}")
async def get_template(
    template_id: str,
    agent: dict = Depends(_require_api_key),
) -> dict:
    """Get a single template by ID (must be owned by caller)."""
    try:
        template = await _get_template(template_id, agent["wallet"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, safe_error(exc, "get_template"))

    # Enrich components with registry metadata
    enriched = []
    for comp in template.get("components", []):
        entry = _COMPONENT_INDEX.get(comp.get("id"))
        enriched.append({
            **comp,
            "name": entry["name"] if entry else "unknown",
            "type": entry["type"] if entry else "unknown",
            "description": entry["description"] if entry else "",
        })
    template["components"] = enriched
    return {"template": template}


@router.post("/templates/{template_id}/activate")
async def activate_template(
    template_id: str,
    agent: dict = Depends(_require_api_key),
) -> dict:
    """Activate an agent template (start running)."""
    template = await _get_template(template_id, agent["wallet"])

    if template["status"] == "active":
        return {"id": template_id, "status": "active", "message": "Already active"}

    # Re-validate pipeline before activation
    errors = _validate_pipeline(template.get("components", []))
    if errors:
        raise HTTPException(
            422,
            {"message": "Cannot activate — pipeline invalid", "validation_errors": errors},
        )

    now = int(time.time())
    try:
        from core.database import db
        await db.raw_execute(
            "UPDATE agent_templates SET status=?, updated_at=? WHERE id=? AND owner_wallet=?",
            ("active", now, template_id, agent["wallet"]),
        )
    except Exception as exc:
        raise HTTPException(500, safe_error(exc, "activate_template"))

    logger.info("Template activated: %s by %s", template_id, agent["wallet"])
    return {"id": template_id, "status": "active", "activated_at": now}


@router.post("/templates/{template_id}/deactivate")
async def deactivate_template(
    template_id: str,
    agent: dict = Depends(_require_api_key),
) -> dict:
    """Pause an active agent template."""
    template = await _get_template(template_id, agent["wallet"])

    if template["status"] == "paused":
        return {"id": template_id, "status": "paused", "message": "Already paused"}
    if template["status"] not in ("active", "error"):
        raise HTTPException(400, f"Cannot deactivate a template in '{template['status']}' status")

    now = int(time.time())
    try:
        from core.database import db
        await db.raw_execute(
            "UPDATE agent_templates SET status=?, updated_at=? WHERE id=? AND owner_wallet=?",
            ("paused", now, template_id, agent["wallet"]),
        )
    except Exception as exc:
        raise HTTPException(500, safe_error(exc, "deactivate_template"))

    logger.info("Template deactivated: %s by %s", template_id, agent["wallet"])
    return {"id": template_id, "status": "paused", "deactivated_at": now}


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    agent: dict = Depends(_require_api_key),
) -> dict:
    """Delete an agent template. Active templates must be deactivated first."""
    template = await _get_template(template_id, agent["wallet"])

    if template["status"] == "active":
        raise HTTPException(400, "Cannot delete an active template. Deactivate it first.")

    try:
        from core.database import db
        await db.raw_execute(
            "DELETE FROM agent_templates WHERE id=? AND owner_wallet=?",
            (template_id, agent["wallet"]),
        )
    except Exception as exc:
        raise HTTPException(500, safe_error(exc, "delete_template"))

    logger.info("Template deleted: %s by %s", template_id, agent["wallet"])
    return {"id": template_id, "deleted": True}
