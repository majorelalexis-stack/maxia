"""MAXIA Sales Agent — lightweight conversational sales agent.

Exposes :class:`MaxiaSalesAgent` which is a drop-in replacement for the
dead SalesGPT library. Same concept (staged funnel + product catalog
grounding) but implemented in pure Python against the local Ollama
server, with zero LangChain dependency.
"""
from .sales_agent import MaxiaSalesAgent, Stage, ConversationState

__all__ = ["MaxiaSalesAgent", "Stage", "ConversationState"]
