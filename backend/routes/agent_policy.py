"""MAXIA Guard Q2b — Routes for the declarative policy.yaml per agent.

Four endpoints under ``/api/agents/{agent_id}/policy``:

    GET    -> return the current YAML (empty string if default)
    PUT    -> upload a new YAML; parse + validate + persist
    DELETE -> reset to the default (no-op) policy
    POST   /validate -> dry-run parse without persisting

Auth: owner-only. Uses X-Admin-Key for now (same as other admin endpoints);
a future iteration can switch to DID signature verification once the intent
envelope flow is wired into the mutation path.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agent-policy"])


async def _get_db():
    from core.database import db
    return db


@router.get("/{agent_id}/policy", response_class=PlainTextResponse)
async def get_policy(agent_id: str, request: Request):
    """Return the current policy YAML (or empty if default)."""
    from core.security import require_admin
    require_admin(request)

    db = await _get_db()
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT policy_yaml FROM agent_permissions WHERE agent_id=? LIMIT 1",
            (agent_id,),
        )
    except Exception as e:
        raise HTTPException(500, f"db error: {type(e).__name__}")

    if not rows:
        raise HTTPException(404, f"agent not found: {agent_id}")

    row = rows[0]
    yaml_text = row["policy_yaml"] if hasattr(row, "keys") else row[0]
    return yaml_text or ""


@router.put("/{agent_id}/policy")
async def put_policy(agent_id: str, request: Request):
    """Install or replace the policy YAML for an agent."""
    from core.security import require_admin
    require_admin(request)

    try:
        body_bytes = await request.body()
    except Exception:
        raise HTTPException(400, "could not read body")
    yaml_text = body_bytes.decode("utf-8", errors="replace")

    from core.policy_engine import save_policy, PolicyError
    db = await _get_db()

    # Make sure the agent exists before writing.
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id FROM agent_permissions WHERE agent_id=? LIMIT 1",
            (agent_id,),
        )
    except Exception as e:
        raise HTTPException(500, f"db error: {type(e).__name__}")
    if not rows:
        raise HTTPException(404, f"agent not found: {agent_id}")

    try:
        policy = await save_policy(db, agent_id, yaml_text)
    except PolicyError as e:
        raise HTTPException(400, {"error": "invalid policy", "detail": str(e)})

    return {
        "agent_id": agent_id,
        "status": "updated",
        "is_default": policy.is_default,
        "version": policy.version,
        "allow_count": len(policy.allow),
        "deny_count": len(policy.deny),
    }


@router.delete("/{agent_id}/policy")
async def delete_policy_route(agent_id: str, request: Request):
    """Reset an agent to the default (no-op) policy."""
    from core.security import require_admin
    require_admin(request)

    from core.policy_engine import delete_policy
    db = await _get_db()
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id FROM agent_permissions WHERE agent_id=? LIMIT 1",
            (agent_id,),
        )
    except Exception as e:
        raise HTTPException(500, f"db error: {type(e).__name__}")
    if not rows:
        raise HTTPException(404, f"agent not found: {agent_id}")

    await delete_policy(db, agent_id)
    return {"agent_id": agent_id, "status": "reset_to_default"}


@router.post("/{agent_id}/policy/validate")
async def validate_policy_route(agent_id: str, request: Request):
    """Dry-run validate a YAML policy without persisting. Admin-auth not
    required — this is a stateless linter endpoint."""
    try:
        body_bytes = await request.body()
    except Exception:
        raise HTTPException(400, "could not read body")
    yaml_text = body_bytes.decode("utf-8", errors="replace")

    from core.policy_engine import parse_policy, PolicyError
    try:
        policy = parse_policy(yaml_text)
    except PolicyError as e:
        raise HTTPException(400, {"error": "invalid policy", "detail": str(e)})

    return {
        "valid": True,
        "is_default": policy.is_default,
        "version": policy.version,
        "allow": sorted(policy.allow),
        "deny": sorted(policy.deny),
        "limits": {
            "max_usdc_per_call": (
                policy.limits.max_usdc_per_call
                if policy.limits.max_usdc_per_call != float("inf") else None
            ),
            "max_usdc_per_day": (
                policy.limits.max_usdc_per_day
                if policy.limits.max_usdc_per_day != float("inf") else None
            ),
            "max_usdc_lifetime": (
                policy.limits.max_usdc_lifetime
                if policy.limits.max_usdc_lifetime != float("inf") else None
            ),
        },
        "constraints": {
            "allowed_chains": sorted(policy.constraints.allowed_chains),
            "denied_tokens": sorted(policy.constraints.denied_tokens),
            "require_2fa_above_usd": (
                policy.constraints.require_2fa_above_usd
                if policy.constraints.require_2fa_above_usd != float("inf") else None
            ),
        },
    }
