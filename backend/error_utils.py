"""Safe error handling — never expose internal details to clients."""
import logging
import uuid

logger = logging.getLogger(__name__)


def safe_error(e: Exception, context: str = "") -> dict:
    """Return a safe error dict for client response. Log full details server-side."""
    req_id = uuid.uuid4().hex[:8]
    logger.error(f"[{req_id}] {context}: {type(e).__name__}: {e}", exc_info=True)
    return {"error": "An error occurred", "request_id": req_id}
