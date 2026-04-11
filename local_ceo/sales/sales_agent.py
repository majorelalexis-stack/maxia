"""MaxiaSalesAgent — staged-funnel sales conversation agent.

Replaces SalesGPT (which is stuck on langchain 0.1.0 and unmaintained).
Same concept:

  1. A 6-stage funnel (intro, qualification, value, needs, objection, closing)
  2. Per-turn stage classification via a cheap classifier prompt
  3. Response generation grounded in ``maxia_catalog.json``
  4. Per-conversation state (history, stage, lang) persisted to SQLite

Design choices:

- Direct Ollama ``POST /api/generate`` instead of LangChain — saves 500ms
  per turn and removes the langchain dependency hell.
- Two LLM calls per turn: one to classify the stage (~100 tokens, <1s),
  one to generate the reply (~300 tokens, ~3s on qwen3:30b-a3b).
- Catalog is injected into the reply prompt verbatim so the model cannot
  invent prices or feature names.
- Post-generation regex filter scrubs sentences that pair a competitor
  name with a percentage (the model still hallucinates Jupiter/AWS fees
  occasionally despite the prompt rule).
- Conversation history is trimmed to the last ``MAX_HISTORY_TURNS`` tours
  to stay well under the 8k ctx window. When the trimmed history is sent,
  a running summary of older turns is prepended.
- Auto language detection (FR/EN) via first-turn heuristic.
- Zero dependency beyond ``httpx`` which is already in the main CEO venv.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

# Names of external services commonly compared against MAXIA.
# When a generated reply contains one of these names AND a numeric price/fee
# in the SAME sentence, the sentence is rewritten to a safe alternative.
#
# Whitelist: Groq, Mistral, Anthropic, OpenAI, Gemini, Cerebras, HuggingFace,
# Replicate — these are LLM providers that MAXIA itself uses under the hood
# via backend/ai/llm_router.py (Cerebras -> Gemini -> Groq -> Mistral -> Claude
# tiered fallback). Citing their rate limits or model names in a MAXIA
# infrastructure context is legitimate, not a competitor pricing leak.
_COMPETITOR_NAMES = (
    "jupiter", "0x", "aws", "bedrock", "azure",
    "fetch", "fetch.ai", "virtuals", "runpod",
    "vast.ai", "lambda",
    "coinbase", "binance", "uniswap", "opensea", "metamask",
)
# Catches percentages, dollar amounts, per-hour rates, decimals.
# We deliberately match a wide net so the post-filter is aggressive on
# competitor sentences. False positives are acceptable because we replace
# the whole sentence with a safe disclaimer rather than dropping it silently.
_NUMBER_RE = re.compile(
    r"(?:\$|€|£|usd\s*)?\d+(?:[\.,]\d+)?\s*(?:%|¢|/h|/hr|/hour|/jour|/mo|/month|usd|eur|dollars?)?",
    re.IGNORECASE,
)

log = logging.getLogger("ceo.sales")

# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

_HERE = Path(__file__).resolve().parent
DEFAULT_CATALOG_PATH = _HERE / "maxia_catalog.json"
DEFAULT_DB_PATH = _HERE / "conversations.db"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:30b-a3b-instruct-2507-q4_K_M"

MAX_HISTORY_TURNS = 10  # beyond this, old turns get summarized
MAX_CATALOG_CHARS = 5500  # 4000 was cutting off enterprise features and HOW TO START
MAX_RESPONSE_TOKENS = 600  # 400 was cutting off multi-step responses mid-sentence
CLASSIFIER_MAX_TOKENS = 20


class Stage(Enum):
    """6-stage MAXIA sales funnel."""
    INTRO = "1_intro"
    QUALIFICATION = "2_qualification"
    VALUE_PROP = "3_value_prop"
    NEEDS_ANALYSIS = "4_needs_analysis"
    OBJECTION_HANDLING = "5_objection_handling"
    CLOSING = "6_closing"


_STAGE_DESCRIPTIONS = {
    Stage.INTRO: "Prospect says hello or first contact. Greet, state who you are in ONE sentence, ask what they are building.",
    Stage.QUALIFICATION: "You need to learn what the prospect is building (solo dev, startup, enterprise) and which chains matter. Ask open questions, listen.",
    Stage.VALUE_PROP: "Map the prospect's needs to 1-2 specific MAXIA features. Cite exact numbers from the catalog. Never invent.",
    Stage.NEEDS_ANALYSIS: "Dig deeper on technical fit: current stack, payment rails, compliance, multi-chain requirements.",
    Stage.OBJECTION_HANDLING: "The prospect raised a concern (price, security, competitors, custody). Respond with specific facts from the catalog.",
    Stage.CLOSING: "The prospect is interested. Propose a concrete next step: register at https://maxiaworld.app/register OR book a demo OR start with the free tier (100 req/day).",
}


# ══════════════════════════════════════════════════════════════════════
# Conversation state
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Turn:
    """A single exchange in the conversation."""
    role: str  # "user" or "bot"
    content: str
    stage: Optional[Stage] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConversationState:
    """All state for one prospect conversation."""
    conversation_id: str
    channel: str  # "telegram", "email", "github", "web"
    user_id: str  # channel-specific user identifier
    stage: Stage = Stage.INTRO
    lang: str = "en"
    history: list[Turn] = field(default_factory=list)
    summary: str = ""  # running summary of turns older than MAX_HISTORY_TURNS
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════
# Persistence (SQLite)
# ══════════════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    user_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    lang TEXT NOT NULL,
    history_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_channel_user ON conversations(channel, user_id);
CREATE INDEX IF NOT EXISTS idx_conv_last_seen ON conversations(last_seen_at);

CREATE TABLE IF NOT EXISTS turns_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    turn_idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    stage TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_conv ON turns_log(conversation_id);
"""


def _get_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def _save_state(conn: sqlite3.Connection, state: ConversationState) -> None:
    history_json = json.dumps(
        [
            {
                "role": t.role,
                "content": t.content,
                "stage": t.stage.value if t.stage else None,
                "timestamp": t.timestamp,
            }
            for t in state.history
        ]
    )
    conn.execute(
        "INSERT OR REPLACE INTO conversations "
        "(conversation_id, channel, user_id, stage, lang, history_json, summary, created_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            state.conversation_id,
            state.channel,
            state.user_id,
            state.stage.value,
            state.lang,
            history_json,
            state.summary,
            state.created_at,
            state.last_seen_at,
        ),
    )
    conn.commit()


def _log_turn(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    turn_idx: int,
    role: str,
    content: str,
    stage: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
) -> None:
    """Append one turn to ``turns_log`` for later analytics."""
    try:
        conn.execute(
            "INSERT INTO turns_log "
            "(conversation_id, turn_idx, role, content, stage, "
            " tokens_in, tokens_out, latency_ms, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                turn_idx,
                role,
                content[:2000],
                stage,
                tokens_in,
                tokens_out,
                latency_ms,
                time.time(),
            ),
        )
        conn.commit()
    except Exception as e:
        log.debug("[MaxiaSalesAgent] turn log failed: %s", e)


def _load_state(conn: sqlite3.Connection, conversation_id: str) -> Optional[ConversationState]:
    row = conn.execute(
        "SELECT channel, user_id, stage, lang, history_json, summary, created_at, last_seen_at "
        "FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not row:
        return None
    history_raw = json.loads(row[4])
    history = [
        Turn(
            role=h["role"],
            content=h["content"],
            stage=Stage(h["stage"]) if h.get("stage") else None,
            timestamp=h["timestamp"],
        )
        for h in history_raw
    ]
    return ConversationState(
        conversation_id=conversation_id,
        channel=row[0],
        user_id=row[1],
        stage=Stage(row[2]),
        lang=row[3],
        history=history,
        summary=row[5],
        created_at=row[6],
        last_seen_at=row[7],
    )


# ══════════════════════════════════════════════════════════════════════
# Main agent
# ══════════════════════════════════════════════════════════════════════

class MaxiaSalesAgent:
    """Staged funnel sales agent grounded in the MAXIA catalog.

    Usage::

        agent = MaxiaSalesAgent()
        reply = await agent.reply("telegram:7780051110", "Bonjour, c'est quoi MAXIA ?")

    Each call updates the persistent conversation state.
    """

    def __init__(
        self,
        catalog_path: Path = DEFAULT_CATALOG_PATH,
        db_path: Path = DEFAULT_DB_PATH,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.5,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.db_path = Path(db_path)
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.temperature = temperature

        if not self.catalog_path.exists():
            raise FileNotFoundError(f"Catalog not found: {self.catalog_path}")
        with self.catalog_path.open("r", encoding="utf-8") as f:
            self._catalog: dict = json.load(f)

        # Pre-compute the compact catalog string injected into every prompt.
        self._catalog_blob = self._build_catalog_blob(self._catalog)

        log.info(
            "[MaxiaSalesAgent] loaded catalog %s (%d chars blob) model=%s",
            self.catalog_path.name,
            len(self._catalog_blob),
            self.model,
        )

    def _build_catalog_blob(self, catalog: dict) -> str:
        """Serialize the catalog to a compact text block for prompt injection."""
        lines: list[str] = []
        company = catalog.get("company", {})
        lines.append(f"COMPANY: {company.get('name', 'MAXIA')} — {company.get('tagline', '')}")
        lines.append(f"WEBSITE: {company.get('website', 'https://maxiaworld.app')}")
        stats = company.get("stats", {})
        if stats:
            lines.append(
                f"STATS: {stats.get('blockchains_live', 15)} chains, "
                f"{stats.get('api_routes', 713)} API routes, "
                f"{stats.get('mcp_tools', 46)} MCP tools, "
                f"{stats.get('ai_services_native', 17)} native AI services, "
                f"{stats.get('tokens_supported', 65)} tokens."
            )
        chains = catalog.get("blockchains_live", [])
        lines.append(f"CHAINS LIVE: {', '.join(chains)}")
        # IMPORTANT: keep the two fee tables visually distant and give the
        # tiers different prefixes so the model does NOT mix them up. This
        # is a direct fix for a hallucination where the 0.10% SWAP fee was
        # quoted as a 0.10% ESCROW fee in an email draft.
        tiers = catalog.get("commission_tiers", {}).get("marketplace_and_escrow", [])
        if tiers:
            lines.append("")
            lines.append("=== FEE STRUCTURE #1 — MARKETPLACE & ESCROW ===")
            lines.append("(paid when an agent BUYS a service from another agent)")
            for t in tiers:
                lines.append(
                    f"  - ESCROW-{t['tier']}: {t['rate_pct']}% "
                    f"for transactions {t['volume_range_usd']} USD"
                )
        swap_tiers = catalog.get("commission_tiers", {}).get("token_swap", [])
        if swap_tiers:
            lines.append("")
            lines.append("=== FEE STRUCTURE #2 — TOKEN SWAP (DIFFERENT FROM ABOVE) ===")
            lines.append("(paid when an agent swaps one crypto token for another)")
            for t in swap_tiers:
                lines.append(f"  - SWAP-{t['tier']}: {t['rate_pct']}%")
            lines.append("NOTE: SWAP fees are SEPARATE from ESCROW fees. Never mix them.")
        escrow = catalog.get("escrow_contracts", {})
        if escrow:
            sol = escrow.get("solana", {})
            base = escrow.get("base", {})
            lines.append(
                f"ESCROW: Solana Anchor PDA {sol.get('program_id', '')[:12]}..., "
                f"Base Solidity {base.get('contract', '')[:12]}..., "
                f"auto-refund {sol.get('auto_refund_hours', 48)}h."
            )
        services = catalog.get("ai_services_native", [])
        if services:
            lines.append(f"AI SERVICES ({len(services)}):")
            for s in services[:12]:
                lines.append(f"  - {s['name']}: {s['desc'][:100]}")
        gpus = catalog.get("gpu_tiers", [])
        if gpus:
            lines.append("GPU RENTAL (via Akash, 15% markup):")
            for g in gpus:
                lines.append(f"  - {g['tier']} {g['vram_gb']}GB: ${g['price_per_hour_usd']}/h")
        free = catalog.get("free_tier", {})
        if free:
            lines.append("")
            lines.append(f"FREE TIER: {free.get('rate_limit', '100 req/day')}")
            feats = free.get("features_included", [])
            if feats:
                for f in feats[:5]:
                    lines.append(f"  - {f}")
        # Enterprise features — critical to avoid the bot claiming MAXIA
        # does not have SSO/OIDC/audit when it actually does.
        enterprise = catalog.get("enterprise_features", [])
        if enterprise:
            lines.append("")
            lines.append(f"ENTERPRISE FEATURES ({len(enterprise)}):")
            for feat in enterprise:
                lines.append(f"  - {feat}")
        # Security grounding — the bot is often asked about trust.
        security = catalog.get("security", {})
        if security:
            lines.append("")
            lines.append("SECURITY:")
            for k, v in security.items():
                lines.append(f"  - {k}: {str(v)[:140]}")
        # Competitor framing — tells the bot HOW to answer "vs X" questions
        # without inventing the other side's numbers.
        diffs = catalog.get("differentiators_vs_competitors", {})
        if diffs:
            lines.append("")
            lines.append("DIFFERENTIATORS (use these to frame vs-competitor "
                         "questions; describe MAXIA's side only):")
            for k, v in diffs.items():
                lines.append(f"  - {k}: {str(v)[:220]}")
        # Competitor pricing policy — explicit canned safe response and
        # forbidden phrase list to suppress invented benchmarks.
        policy = catalog.get("competitor_pricing_policy", {})
        if policy:
            lines.append("")
            lines.append("COMPETITOR PRICING POLICY (CRITICAL):")
            if policy.get("rule"):
                lines.append(f"  RULE: {policy['rule']}")
            if policy.get("safe_response_pattern"):
                lines.append(f"  SAFE PATTERN: {policy['safe_response_pattern']}")
            forbidden = policy.get("forbidden_phrases", [])
            if forbidden:
                lines.append("  FORBIDDEN PHRASES (never produce these):")
                for ph in forbidden:
                    lines.append(f"    * {ph}")
        how = catalog.get("how_to_start", {})
        if how:
            lines.append("")
            lines.append("HOW TO START:")
            for k, v in how.items():
                lines.append(f"  {k}: {v}")
        never = catalog.get("never_say", [])
        if never:
            lines.append("")
            lines.append("NEVER SAY:")
            for n in never:
                lines.append(f"  - {n}")
        blob = "\n".join(lines)
        if len(blob) > MAX_CATALOG_CHARS:
            blob = blob[:MAX_CATALOG_CHARS] + "\n... (truncated)"
        return blob

    # ── Public API ────────────────────────────────────────────────

    async def reply(
        self,
        conversation_id: str,
        user_message: str,
        channel: str = "telegram",
        user_id: str = "",
        lang: Optional[str] = None,
    ) -> tuple[str, Stage]:
        """Process one user message and return the bot reply + current stage.

        Args:
            conversation_id: unique ID per ongoing chat (e.g. ``telegram:7780051110``).
            user_message: the incoming text from the prospect.
            channel: ``telegram`` | ``email`` | ``github`` | ``web``.
            user_id: channel-specific identifier (extracted from conversation_id if empty).
            lang: force a language code. If None, auto-detected on first turn.

        Returns:
            Tuple of (bot_reply_text, current_stage).
        """
        t0 = time.time()
        conn = _get_db(self.db_path)
        try:
            state = _load_state(conn, conversation_id)
            if state is None:
                if not user_id and ":" in conversation_id:
                    user_id = conversation_id.split(":", 1)[1]
                state = ConversationState(
                    conversation_id=conversation_id,
                    channel=channel,
                    user_id=user_id,
                    stage=Stage.INTRO,
                    lang=lang or self._detect_lang(user_message),
                )
                log.info("[MaxiaSalesAgent] new conversation %s (%s)", conversation_id, state.lang)

            # Snapshot the turn index BEFORE appending the user message so
            # the persisted user/bot pair share consecutive ``turn_idx`` values.
            turn_idx_user = len(state.history)

            # Append user turn (in-memory) and persist a turns_log row
            state.history.append(Turn(role="user", content=user_message, stage=state.stage))
            state.last_seen_at = time.time()
            _log_turn(
                conn,
                conversation_id=conversation_id,
                turn_idx=turn_idx_user,
                role="user",
                content=user_message,
                stage=state.stage.value,
            )

            # Classify new stage based on user message + current stage
            new_stage = await self._classify_stage(state, user_message)
            if new_stage != state.stage:
                log.info(
                    "[MaxiaSalesAgent] stage %s -> %s (%s)",
                    state.stage.value, new_stage.value, conversation_id,
                )
                state.stage = new_stage

            # Generate reply grounded in the catalog
            reply_text = await self._generate_reply(state)

            # Append bot turn (in-memory) + log to telemetry table
            turn_idx_bot = len(state.history)
            state.history.append(Turn(role="bot", content=reply_text, stage=state.stage))
            latency_ms = int((time.time() - t0) * 1000)
            _log_turn(
                conn,
                conversation_id=conversation_id,
                turn_idx=turn_idx_bot,
                role="bot",
                content=reply_text,
                stage=state.stage.value,
                # Rough character-based proxy for tokens — 1 token ~= 4 chars
                tokens_in=len(user_message) // 4,
                tokens_out=len(reply_text) // 4,
                latency_ms=latency_ms,
            )

            # Summarize old turns if history is getting long
            if len(state.history) > MAX_HISTORY_TURNS * 2:
                await self._compact_history(state)

            _save_state(conn, state)

            log.debug(
                "[MaxiaSalesAgent] reply %s stage=%s len=%d lat=%dms",
                conversation_id, state.stage.value, len(reply_text), latency_ms,
            )
            return reply_text, state.stage
        finally:
            conn.close()

    def get_state(self, conversation_id: str) -> Optional[ConversationState]:
        """Read-only access to the persisted state of a conversation."""
        conn = _get_db(self.db_path)
        try:
            return _load_state(conn, conversation_id)
        finally:
            conn.close()

    # ── LLM helpers ───────────────────────────────────────────────

    @staticmethod
    def _scrub_competitor_pricing(text: str) -> str:
        """Strip sentences that pair a competitor name with a percentage.

        Last-resort guardrail for the residual hallucination where the model
        invents Jupiter/AWS/0x fees despite the prompt rules. We don't try
        to be clever — we just neutralize the offending sentence.
        """
        if not isinstance(text, str) or not text:
            return text
        # Split into sentences while preserving paragraph breaks
        out_parts: list[str] = []
        replaced = 0
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                out_parts.append(paragraph)
                continue
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            new_sentences: list[str] = []
            for sent in sentences:
                low = sent.lower()
                has_competitor = any(name in low for name in _COMPETITOR_NAMES)
                has_number = bool(_NUMBER_RE.search(sent))
                if has_competitor and has_number:
                    # Skip whole sentence — replace with neutral fallback
                    replaced += 1
                    new_sentences.append(
                        "(I cannot quote competitor pricing reliably; please "
                        "check their official docs.)"
                    )
                else:
                    new_sentences.append(sent)
            out_parts.append(" ".join(new_sentences))
        cleaned = "\n".join(out_parts)
        if replaced:
            log.info("[MaxiaSalesAgent] scrubbed %d competitor-pricing sentence(s)", replaced)
        return cleaned

    @staticmethod
    def _detect_lang(text: str) -> str:
        """Cheap FR/EN heuristic. Good enough for first-turn routing."""
        fr_markers = (
            " le ", " la ", " les ", " un ", " une ", " des ", " c'est ",
            " j'", " tu ", " vous ", " nous ", " est-ce ", " avec ",
            "bonjour", "salut", "merci", "combien", "comment",
        )
        t = f" {text.lower()} "
        return "fr" if any(m in t for m in fr_markers) else "en"

    async def _classify_stage(self, state: ConversationState, user_message: str) -> Stage:
        """Ask the LLM which stage the conversation is at after the new message."""
        stage_list = "\n".join(
            f"  {s.value}: {_STAGE_DESCRIPTIONS[s][:80]}" for s in Stage
        )
        history_tail = "\n".join(
            f"{t.role.upper()}: {t.content[:200]}" for t in state.history[-4:]
        )
        prompt = (
            "You are classifying the current stage of a sales conversation about MAXIA, "
            "an AI-to-AI marketplace.\n\n"
            "Stages:\n"
            f"{stage_list}\n\n"
            f"Current stage: {state.stage.value}\n\n"
            "Recent conversation:\n"
            f"{history_tail}\n\n"
            "Based on the LATEST user message, output ONLY the stage identifier "
            f"(one of: {', '.join(s.value for s in Stage)}). No explanation, just the identifier."
        )
        try:
            raw = await self._ollama_generate(
                prompt=prompt,
                system="You classify sales conversation stages. Output only the stage ID.",
                max_tokens=CLASSIFIER_MAX_TOKENS,
                temperature=0.0,
            )
        except Exception as e:
            log.warning("[MaxiaSalesAgent] classify error: %s — keeping %s", e, state.stage.value)
            return state.stage

        raw = raw.strip().lower()
        for s in Stage:
            if s.value in raw:
                return s
        return state.stage

    def _retrieve_rag_supplement(self, user_message: str) -> str:
        """Pull supplementary RAG context for off-catalog prospect questions.

        Runs only when ``ENABLE_SALES_RAG=1`` is set. The retrieved
        chunks are injected *after* the catalog so the catalog remains
        the primary source of truth for the triple-defense anti-halluc
        stack (catalog prefixes, BAD/GOOD examples, _scrub regex).

        Kept short (≤1200 chars) so the system prompt doesn't explode
        past ~8 KB and slow qwen3 generation below the 4 s p95 target.
        """
        if os.getenv("ENABLE_SALES_RAG", "0") != "1":
            return ""
        if not isinstance(user_message, str) or not user_message.strip():
            return ""
        try:
            import sys as _sys
            _here = Path(__file__).parent.parent
            if str(_here) not in _sys.path:
                _sys.path.insert(0, str(_here))
            from rag_knowledge import build_rag_context
        except ImportError:
            return ""
        try:
            return build_rag_context(query=user_message, max_chars=1200)
        except Exception as e:  # defensive: RAG must never break sales
            log.warning("[MaxiaSalesAgent] RAG supplement failed: %s", e)
            return ""

    async def _generate_reply(self, state: ConversationState) -> str:
        """Build the reply prompt with catalog grounding and generate."""
        stage_hint = _STAGE_DESCRIPTIONS[state.stage]

        # Latest prospect message is the query for the RAG supplement
        last_user_msg = ""
        for t in reversed(state.history):
            if t.role == "user":
                last_user_msg = t.content
                break
        rag_supplement = self._retrieve_rag_supplement(last_user_msg)
        if rag_supplement:
            log.info(
                "[MaxiaSalesAgent] RAG supplement injected: %d chars for conv=%s",
                len(rag_supplement), state.conversation_id,
            )

        system = (
            f"You are the MAXIA CEO Assistant, a professional AI Business "
            f"Development Representative. You speak {'French' if state.lang == 'fr' else 'English'} "
            f"naturally and respond to prospects about the MAXIA AI-to-AI marketplace.\n\n"
            "HARD RULES (violating any of these will cause the reply to be rejected):\n"
            "1. NEVER invent numbers, prices, features, chains, or partner names. "
            "Use ONLY the catalog below. If a fact is not in the catalog, say "
            "'I will check with Alexis and get back to you' instead of guessing.\n"
            "2. NEVER cite a competitor's fee, price, or percentage — even "
            "as a range, even as 'typically', even as 'around', even if the "
            "user explicitly asks. You do NOT have reliable benchmarks. "
            "Concrete examples:\n"
            "   * BAD: 'Jupiter typically charges 0.05-0.3% for swaps'\n"
            "   * BAD: 'competitive with Jupiter's routing fees'\n"
            "   * BAD: 'lower than AWS Bedrock pricing'\n"
            "   * GOOD: 'I can't speak to Jupiter's pricing — please check "
            "their docs. What I can tell you is MAXIA's swap fee is "
            "0.10% BRONZE down to 0.01% WHALE.'\n"
            "   * GOOD: 'I don't have reliable numbers for AWS Bedrock. "
            "MAXIA's GPU rental via Akash starts at $0.25/h for a T4.'\n"
            "   When asked 'how do you compare to Jupiter/0x/AWS', describe "
            "ONLY the MAXIA side using catalog numbers, then suggest the user "
            "checks the competitor's official docs for theirs.\n"
            "3. MAXIA has TWO separate fee structures — do NOT mix them:\n"
            "   * ESCROW fees (marketplace trades): BRONZE 1.5% / GOLD 0.5% / WHALE 0.1%\n"
            "   * SWAP fees (token exchanges): BRONZE 0.10% / SILVER 0.05% / "
            "GOLD 0.03% / WHALE 0.01%\n"
            "   When the user asks about fees, ALWAYS name which structure you "
            "are quoting (say 'escrow' or 'swap' explicitly).\n"
            "4. NEVER use hype words: revolutionary, game-changing, moon, lambo, "
            "100x, guaranteed, insane, mind-blowing, disruptive.\n"
            "5. NEVER share: user counts, MRR, ARR, revenue, wallet balances, "
            "funding, internal metrics. If asked, say 'we don't share those "
            "publicly'.\n"
            "6. NEVER bash competitors. Compare fairly with catalog facts only.\n"
            "7. Be concise: 2-4 sentences max UNLESS the user asks for "
            "detail, enumeration, a complete list, or 'all' items — in that "
            "case, include EVERY relevant item from the catalog in full "
            "(e.g. all 15 blockchains, the full LLM fallback chain with "
            "every tier from LOCAL to STRATEGIC, all 17 AI services, etc.). "
            "Never truncate a list the user explicitly asked for.\n"
            "8. Include https://maxiaworld.app when pointing to onboarding or docs.\n"
            "9. Professional, calm, confident tone. No emoji.\n\n"
            "CATALOG (ground truth, do NOT invent anything outside of this):\n"
            f"{self._catalog_blob}\n\n"
            + (
                "ADDITIONAL CONTEXT (semantic retrieval — supplements but "
                "NEVER overrides the catalog above for prices, tiers, or "
                "fees):\n"
                f"{rag_supplement}\n\n"
                if rag_supplement
                else ""
            )
            + f"CURRENT STAGE: {state.stage.value} — {stage_hint}"
        )

        convo_lines: list[str] = []
        if state.summary:
            convo_lines.append(f"[Earlier conversation summary]: {state.summary}")
        for t in state.history[-MAX_HISTORY_TURNS:]:
            role = "Prospect" if t.role == "user" else "You"
            convo_lines.append(f"{role}: {t.content}")
        convo_lines.append("You:")
        prompt = "\n".join(convo_lines)

        reply = await self._ollama_generate(
            prompt=prompt,
            system=system,
            max_tokens=MAX_RESPONSE_TOKENS,
            temperature=self.temperature,
        )
        # Strip "You:" prefix if the model echoed it
        for junk in ("You:", "Bot:", "Assistant:", "MAXIA:"):
            if reply.strip().startswith(junk):
                reply = reply.strip()[len(junk):].strip()
                break
        # Last-resort post-filter: strip any sentence that pairs a competitor
        # name with a percentage. The model still hallucinates Jupiter/AWS
        # fees occasionally even with the explicit prompt rules.
        reply = self._scrub_competitor_pricing(reply)
        return reply.strip()

    async def _compact_history(self, state: ConversationState) -> None:
        """Summarize the oldest half of the history and replace with a summary."""
        to_summarize = state.history[:-MAX_HISTORY_TURNS]
        keep = state.history[-MAX_HISTORY_TURNS:]
        if not to_summarize:
            return
        dialogue = "\n".join(
            f"{t.role.upper()}: {t.content[:300]}" for t in to_summarize
        )
        prompt = (
            "Summarize the following sales conversation in 2-3 sentences, "
            "keeping the prospect's needs, objections, and any commitments:\n\n"
            f"{dialogue}\n\nSummary:"
        )
        try:
            summary = await self._ollama_generate(
                prompt=prompt,
                system="You write concise conversation summaries.",
                max_tokens=200,
                temperature=0.3,
            )
        except Exception as e:
            log.warning("[MaxiaSalesAgent] summary failed: %s", e)
            return
        if state.summary:
            state.summary = f"{state.summary}\n{summary.strip()}"
        else:
            state.summary = summary.strip()
        state.history = keep

    async def _ollama_generate(
        self,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Single Ollama /api/generate call."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": 8192,
            },
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self.ollama_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
