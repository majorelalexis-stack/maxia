"""MAXIA Guard — Pillar 3 extension: declarative Policy YAML.

Each agent can install a ``policy.yaml`` document that restricts what it is
allowed to do. The policy is evaluated **before** OAuth scopes and budget
caps, so a deny in the policy shorts the request at the earliest point.

Supported YAML schema (version 1)::

    version: 1
    allow: [swap:execute, read:prices]         # optional allow-list
    deny:  [escrow:create, transfer_large]      # optional deny-list
    limits:
      max_usdc_per_call: 10
      max_usdc_per_day:  50
      max_usdc_lifetime: 500
    constraints:
      allowed_chains: [solana, base]
      denied_tokens:  [PUMP, TRUMP]
      require_2fa_above_usd: 100

Evaluation order:
    1. Explicit ``deny`` list hits  -> DENY.
    2. Chain not in ``allowed_chains`` -> DENY.
    3. Token in ``denied_tokens`` -> DENY.
    4. Per-call limit exceeded -> DENY.
    5. ``allow`` list non-empty and action not in it -> DENY.
    6. Otherwise -> ALLOW (with optional ``require_2fa`` flag set).

Denies never raise on their own — ``evaluate()`` returns a ``Decision`` and
the caller (``agent_permissions.check_policy``) is responsible for raising
``HTTPException(403)``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

MAX_POLICY_BYTES = 10 * 1024  # 10 KB — reject larger uploads
SUPPORTED_VERSION = 1

_VALID_CHAINS = {
    "solana", "base", "ethereum", "polygon", "arbitrum", "avalanche",
    "bnb", "tron", "ton", "xrp", "sui", "near", "aptos", "sei", "bitcoin",
}


# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyLimits:
    max_usdc_per_call: float = float("inf")
    max_usdc_per_day: float = float("inf")
    max_usdc_lifetime: float = float("inf")


@dataclass(frozen=True)
class PolicyConstraints:
    allowed_chains: frozenset[str] = field(default_factory=frozenset)
    denied_tokens: frozenset[str] = field(default_factory=frozenset)
    require_2fa_above_usd: float = float("inf")


@dataclass(frozen=True)
class Policy:
    version: int
    allow: frozenset[str]
    deny: frozenset[str]
    limits: PolicyLimits
    constraints: PolicyConstraints

    @property
    def is_default(self) -> bool:
        """A policy is 'default' (no-op) if nothing is restricted."""
        return (
            not self.allow
            and not self.deny
            and self.limits.max_usdc_per_call == float("inf")
            and self.limits.max_usdc_per_day == float("inf")
            and self.limits.max_usdc_lifetime == float("inf")
            and not self.constraints.allowed_chains
            and not self.constraints.denied_tokens
            and self.constraints.require_2fa_above_usd == float("inf")
        )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    rule: str = ""
    require_2fa: bool = False


_DEFAULT_POLICY = Policy(
    version=SUPPORTED_VERSION,
    allow=frozenset(),
    deny=frozenset(),
    limits=PolicyLimits(),
    constraints=PolicyConstraints(),
)


# ── Parsing ────────────────────────────────────────────────────────────


class PolicyError(ValueError):
    """Raised when a policy YAML fails to parse or validate."""


def _to_str_set(value, field_name: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple)):
        raise PolicyError(f"{field_name}: expected list, got {type(value).__name__}")
    out = set()
    for item in value:
        if not isinstance(item, str):
            raise PolicyError(f"{field_name}: items must be strings")
        s = item.strip()
        if s:
            out.add(s)
    return frozenset(out)


def _to_float(value, field_name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise PolicyError(f"{field_name}: must be a number, not a boolean")
    if not isinstance(value, (int, float)):
        raise PolicyError(f"{field_name}: must be a number")
    f = float(value)
    if f < 0:
        raise PolicyError(f"{field_name}: must be non-negative")
    return f


def parse_policy(text: str) -> Policy:
    """Parse a YAML policy string into a ``Policy``. Raises ``PolicyError``.

    Uses ``yaml.safe_load`` — code execution is impossible.
    """
    if text is None:
        return _DEFAULT_POLICY
    if not isinstance(text, str):
        raise PolicyError("policy must be a string")
    if len(text.encode("utf-8")) > MAX_POLICY_BYTES:
        raise PolicyError(f"policy too large (max {MAX_POLICY_BYTES} bytes)")
    if not text.strip():
        return _DEFAULT_POLICY

    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception as e:
        raise PolicyError(f"invalid YAML: {e}") from e

    if data is None:
        return _DEFAULT_POLICY
    if not isinstance(data, dict):
        raise PolicyError("policy root must be a mapping")

    version = data.get("version", 1)
    if version != SUPPORTED_VERSION:
        raise PolicyError(f"unsupported policy version: {version}")

    allow = _to_str_set(data.get("allow"), "allow")
    deny = _to_str_set(data.get("deny"), "deny")

    limits_raw = data.get("limits") or {}
    if not isinstance(limits_raw, dict):
        raise PolicyError("limits: must be a mapping")
    limits = PolicyLimits(
        max_usdc_per_call=_to_float(limits_raw.get("max_usdc_per_call"),
                                     "limits.max_usdc_per_call", float("inf")),
        max_usdc_per_day=_to_float(limits_raw.get("max_usdc_per_day"),
                                    "limits.max_usdc_per_day", float("inf")),
        max_usdc_lifetime=_to_float(limits_raw.get("max_usdc_lifetime"),
                                     "limits.max_usdc_lifetime", float("inf")),
    )

    constraints_raw = data.get("constraints") or {}
    if not isinstance(constraints_raw, dict):
        raise PolicyError("constraints: must be a mapping")
    allowed_chains_raw = _to_str_set(constraints_raw.get("allowed_chains"),
                                      "constraints.allowed_chains")
    for chain in allowed_chains_raw:
        if chain.lower() not in _VALID_CHAINS:
            raise PolicyError(f"constraints.allowed_chains: unknown chain {chain!r}")
    allowed_chains = frozenset(c.lower() for c in allowed_chains_raw)
    denied_tokens = frozenset(
        t.upper() for t in _to_str_set(constraints_raw.get("denied_tokens"),
                                        "constraints.denied_tokens")
    )
    require_2fa = _to_float(
        constraints_raw.get("require_2fa_above_usd"),
        "constraints.require_2fa_above_usd",
        float("inf"),
    )

    return Policy(
        version=SUPPORTED_VERSION,
        allow=allow,
        deny=deny,
        limits=limits,
        constraints=PolicyConstraints(
            allowed_chains=allowed_chains,
            denied_tokens=denied_tokens,
            require_2fa_above_usd=require_2fa,
        ),
    )


# ── Evaluation ─────────────────────────────────────────────────────────


def _matches_scope_list(action: str, scope_list: frozenset[str]) -> bool:
    """Wildcard-aware membership check for scopes like ``swap:*``."""
    if action in scope_list:
        return True
    parts = action.split(":")
    for s in scope_list:
        if s == "*":
            return True
        s_parts = s.split(":")
        if len(s_parts) == 2 and s_parts[1] == "*" and s_parts[0] == parts[0]:
            return True
    return False


def evaluate(
    policy: Policy,
    action: str,
    *,
    amount_usdc: float = 0.0,
    chain: str = "",
    token: str = "",
) -> Decision:
    """Evaluate an action against a ``Policy``.

    The caller should translate a ``Decision(allowed=False, ...)`` into an
    HTTPException(403) with the ``reason`` and ``rule`` in the payload.
    """
    if policy.is_default:
        return Decision(allowed=True)

    # 1. Explicit deny list.
    if policy.deny and _matches_scope_list(action, policy.deny):
        return Decision(
            allowed=False,
            rule="policy.deny",
            reason=f"Action {action!r} is in policy deny-list",
        )

    # 2. Chain constraint.
    if policy.constraints.allowed_chains and chain:
        if chain.lower() not in policy.constraints.allowed_chains:
            return Decision(
                allowed=False,
                rule="policy.constraints.allowed_chains",
                reason=f"Chain {chain!r} is not in the allow-list",
            )

    # 3. Denied tokens.
    if policy.constraints.denied_tokens and token:
        if token.upper() in policy.constraints.denied_tokens:
            return Decision(
                allowed=False,
                rule="policy.constraints.denied_tokens",
                reason=f"Token {token!r} is denied by policy",
            )

    # 4. Per-call limit.
    if amount_usdc and amount_usdc > policy.limits.max_usdc_per_call:
        return Decision(
            allowed=False,
            rule="policy.limits.max_usdc_per_call",
            reason=f"Amount {amount_usdc} USDC exceeds per-call limit "
                   f"{policy.limits.max_usdc_per_call}",
        )

    # 5. Allow-list.
    if policy.allow and not _matches_scope_list(action, policy.allow):
        return Decision(
            allowed=False,
            rule="policy.allow",
            reason=f"Action {action!r} is not in policy allow-list",
        )

    # 6. require_2fa flag (doesn't deny, just signals).
    require_2fa = bool(
        amount_usdc
        and amount_usdc > policy.constraints.require_2fa_above_usd
    )
    return Decision(allowed=True, require_2fa=require_2fa)


# ── Policy storage / cache ─────────────────────────────────────────────

# Compiled-policy cache keyed by agent_id. Invalidated on upsert/delete.
_policy_cache: dict[str, Policy] = {}


def cache_set(agent_id: str, policy: Policy) -> None:
    _policy_cache[agent_id] = policy


def cache_get(agent_id: str) -> Optional[Policy]:
    return _policy_cache.get(agent_id)


def cache_invalidate(agent_id: str) -> None:
    _policy_cache.pop(agent_id, None)


def cache_clear() -> None:
    _policy_cache.clear()


async def load_policy(db, agent_id: str) -> Policy:
    """Load and cache the policy for an agent. Falls back to default
    policy on any error or when the column is empty/NULL."""
    cached = cache_get(agent_id)
    if cached is not None:
        return cached

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT policy_yaml FROM agent_permissions WHERE agent_id=? LIMIT 1",
            (agent_id,),
        )
    except Exception as e:
        logger.debug("load_policy: db error for %s: %s", agent_id, e)
        cache_set(agent_id, _DEFAULT_POLICY)
        return _DEFAULT_POLICY

    if not rows:
        cache_set(agent_id, _DEFAULT_POLICY)
        return _DEFAULT_POLICY

    row = rows[0]
    yaml_text = row["policy_yaml"] if hasattr(row, "keys") else row[0]
    if not yaml_text:
        cache_set(agent_id, _DEFAULT_POLICY)
        return _DEFAULT_POLICY

    try:
        policy = parse_policy(yaml_text)
    except PolicyError as e:
        logger.warning("load_policy: invalid YAML for %s: %s", agent_id, e)
        cache_set(agent_id, _DEFAULT_POLICY)
        return _DEFAULT_POLICY

    cache_set(agent_id, policy)
    return policy


async def save_policy(db, agent_id: str, yaml_text: str) -> Policy:
    """Validate + store a policy YAML for an agent. Raises ``PolicyError``
    on invalid input."""
    policy = parse_policy(yaml_text)  # raises on invalid
    try:
        await db.raw_execute(
            "UPDATE agent_permissions SET policy_yaml=? WHERE agent_id=?",
            (yaml_text, agent_id),
        )
    except Exception as e:
        raise PolicyError(f"failed to persist policy: {e}") from e
    cache_invalidate(agent_id)
    return policy


async def delete_policy(db, agent_id: str) -> None:
    """Reset an agent to the default (no-op) policy."""
    try:
        await db.raw_execute(
            "UPDATE agent_permissions SET policy_yaml='' WHERE agent_id=?",
            (agent_id,),
        )
    except Exception as e:
        logger.warning("delete_policy: db error for %s: %s", agent_id, e)
    cache_invalidate(agent_id)
