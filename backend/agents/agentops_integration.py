"""AgentOps Integration — Observability pour MAXIA marketplace

Fonctionnalites :
- Auto-instrumentation LLM (Cerebras/Gemini/Groq/Anthropic/Mistral)
- Traces par operation (swap, bridge, service execution, GPU rental)
- Events custom pour chaque type d'action marketplace
- Cost tracking par LLM provider
- Graceful : desactive si AGENTOPS_API_KEY absent
"""
import logging
import os
import time
from typing import Optional, Any

logger = logging.getLogger(__name__)

_enabled = False


def init_agentops() -> bool:
    """Initialise AgentOps. Appeler AVANT l'import des clients LLM.

    Returns True si actif, False sinon.
    """
    global _enabled
    api_key = os.environ.get("AGENTOPS_API_KEY", "")
    if not api_key:
        logger.info("[AgentOps] AGENTOPS_API_KEY absent — observability desactivee")
        return False
    try:
        import agentops
        agentops.init(
            api_key=api_key,
            default_tags=["maxia", os.environ.get("ENVIRONMENT", "prod")],
            auto_start_session=False,
            instrument_llm_calls=True,
        )
        _enabled = True
        logger.info("[AgentOps] Initialise — auto-instrumentation LLM active")
        return True
    except Exception as e:
        logger.warning("[AgentOps] Init failed: %s — desactive", e)
        return False


def shutdown_agentops() -> None:
    """Ferme toutes les traces. Appeler au shutdown."""
    if not _enabled:
        return
    try:
        import agentops
        agentops.end_all_sessions()
        logger.info("[AgentOps] Toutes les traces fermees")
    except Exception as e:
        logger.warning("[AgentOps] Shutdown error: %s", e)


def start_trace(tags: Optional[list] = None):
    """Demarre une trace AgentOps. Retourne la trace ou None."""
    if not _enabled:
        return None
    try:
        import agentops
        return agentops.start_trace(tags=tags or [])
    except Exception as e:
        logger.warning("[AgentOps] start_trace error: %s", e)
        return None


def end_trace(trace, end_state: str = "Success", reason: str = "") -> None:
    """Ferme une trace AgentOps."""
    if trace is None:
        return
    try:
        import agentops
        agentops.end_trace(trace, end_state=end_state)
    except Exception as e:
        logger.warning("[AgentOps] end_trace error: %s", e)


# ═══════════════════════════════════════
# Custom event recording
# ═══════════════════════════════════════

def record_event(event_type: str, data: Optional[dict] = None) -> None:
    """Record a custom event in AgentOps."""
    if not _enabled:
        return
    try:
        import agentops
        agentops.record(agentops.ActionEvent(
            action_type=event_type,
            params=data or {},
            returns={"status": "recorded"},
        ))
    except Exception as e:
        logger.debug("[AgentOps] record_event error: %s", e)


def record_llm_call(provider: str, model: str, tokens_in: int, tokens_out: int,
                     duration_ms: float, cost_usd: float = 0.0) -> None:
    """Record an LLM call with cost tracking."""
    if not _enabled:
        return
    try:
        import agentops
        agentops.record(agentops.ActionEvent(
            action_type="llm_call",
            params={
                "provider": provider,
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
            returns={
                "duration_ms": round(duration_ms, 1),
                "cost_usd": round(cost_usd, 6),
            },
        ))
    except Exception:
        pass


def record_swap(from_token: str, to_token: str, amount: float,
                chain: str = "solana", commission_usd: float = 0.0) -> None:
    """Record a token swap event."""
    record_event("swap", {
        "from_token": from_token,
        "to_token": to_token,
        "amount": amount,
        "chain": chain,
        "commission_usd": commission_usd,
    })


def record_bridge(from_chain: str, to_chain: str, token: str,
                   amount: float, protocol: str = "") -> None:
    """Record a cross-chain bridge event."""
    record_event("bridge", {
        "from_chain": from_chain,
        "to_chain": to_chain,
        "token": token,
        "amount": amount,
        "protocol": protocol,
    })


def record_service_execution(service_type: str, cost_usdc: float,
                              agent_name: str = "", duration_ms: float = 0.0) -> None:
    """Record an AI service execution."""
    record_event("service_execution", {
        "service_type": service_type,
        "cost_usdc": cost_usdc,
        "agent": agent_name,
        "duration_ms": round(duration_ms, 1),
    })


def record_gpu_rental(tier: str, hours: float, cost_usdc: float) -> None:
    """Record a GPU rental event."""
    record_event("gpu_rental", {
        "tier": tier,
        "hours": hours,
        "cost_usdc": cost_usdc,
    })


def record_mcp_tool_call(tool_name: str, duration_ms: float,
                          success: bool = True) -> None:
    """Record an MCP tool call."""
    record_event("mcp_tool_call", {
        "tool": tool_name,
        "duration_ms": round(duration_ms, 1),
        "success": success,
    })


def record_error(action_type: str, exception: Exception,
                  context: Optional[dict] = None) -> None:
    """Record an error event."""
    if not _enabled:
        return
    try:
        import agentops
        agentops.record(agentops.ErrorEvent(
            error_type=action_type,
            details=str(exception)[:500],
        ))
    except Exception:
        pass


# ═══════════════════════════════════════
# Observability endpoint data
# ═══════════════════════════════════════

def get_observability_status() -> dict:
    """Return AgentOps status for /health and dashboard."""
    return {
        "enabled": _enabled,
        "provider": "agentops",
        "dashboard": "https://app.agentops.ai" if _enabled else None,
        "features": {
            "llm_tracking": _enabled,
            "event_recording": _enabled,
            "session_replay": _enabled,
            "cost_tracking": _enabled,
        },
    }
