"""AgentOps Integration — Observability pour les 17 agents MAXIA

Fonctionnalites :
- Auto-instrumentation LLM (Groq/Anthropic/Mistral) si init() avant import clients
- Traces par boucle CEO (tactique/strategique/vision/expansion)
- Events custom pour trades, swaps, GPU, escrow
- Graceful : desactive si AGENTOPS_API_KEY absent
"""
import logging
import os
from typing import Optional

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
        # end_all_sessions couvre aussi les traces legacy
        agentops.end_all_sessions()
        logger.info("[AgentOps] Toutes les traces fermees")
    except Exception as e:
        logger.warning("[AgentOps] Shutdown error: %s", e)


def start_trace(tags: Optional[list] = None):
    """Demarre une trace AgentOps (v4). Retourne la trace ou None."""
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


def record_error(action_type: str, exception: Exception) -> None:
    """Enregistre une erreur."""
    if not _enabled:
        return
    try:
        logger.warning("[AgentOps] Error in %s: %s", action_type, exception)
    except Exception:
        pass
