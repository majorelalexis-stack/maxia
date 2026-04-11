"""Mission — Telegram Chat: interact with Alexis via @MAXIA_AI_bot.

Polls Telegram Bot API. Two routing modes per message:

  * **Alexis chat** (``chat.id == TELEGRAM_CEO_CHAT_ID``) — legacy
    knowledge-grounded response via the CHAT agent. Handles slash
    commands, callback approvals, and Alexis's own questions.

  * **Prospect chat** (any other ``chat.id``) — when
    ``ENABLE_TELEGRAM_PROSPECTS=1``, the message is routed through
    :class:`sales.MaxiaSalesAgent` via the smart_reply library, which
    maintains a per-prospect staged funnel and grounds replies in
    ``sales/maxia_catalog.json``. The reply is sent BACK to the prospect
    chat (not Alexis's chat). When the conversation reaches stage
    ``6_closing``, Alexis is alerted on his own chat so he can step in.

Rate limiting: each prospect ``from.id`` is capped at
``PROSPECT_RATE_LIMIT_PER_HOUR`` messages. Above that, the bot replies
with a brief throttle message and skips the LLM call entirely.

Sensitive escalation: messages matching the ``vps_bridge`` keyword regex
(refund, lawsuit, hack, etc., 13 languages) are NEVER answered by the
agent — they get a "Alexis will follow up" canned reply and Alexis is
notified. Same logic as ``vps_bridge`` for consistency across channels.
"""
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

import httpx

from agents import CHAT, MAXIA_KNOWLEDGE
from config_local import VPS_URL
from llm import ask

log = logging.getLogger("ceo")

_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CEO_CHAT_ID = os.getenv("TELEGRAM_CEO_CHAT_ID", "")
# Alexis's user_id. Needed so his DMs with @MAXIA_AI_bot still route to
# the CEO command handlers even though _TELEGRAM_CEO_CHAT_ID now points
# to the separate @MAXIA_alerts channel for approvals/reports.
_TELEGRAM_ALEXIS_USER_ID = os.getenv("TELEGRAM_ALEXIS_USER_ID", "")
_TELEGRAM_API = "https://api.telegram.org/bot"
_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(__file__))
_STATE_FILE = os.path.join(_LOCAL_CEO_DIR, "telegram_state.json")
_POLL_TIMEOUT = 5

# Feature flag — default ON now that the wiring exists. Set to 0 in .env
# to fall back to "ignore non-Alexis messages" if the agent goes haywire.
_ENABLE_PROSPECTS = os.getenv("ENABLE_TELEGRAM_PROSPECTS", "1") == "1"

# Per-prospect rate limit. Sliding window over the last hour. Above this
# the bot sends a throttle message instead of calling the LLM, protecting
# the GPU and the SMTP/Telegram quota.
PROSPECT_RATE_LIMIT_PER_HOUR = int(os.getenv("TELEGRAM_PROSPECT_RPH", "30"))
_prospect_msg_log: dict[int, deque] = defaultdict(deque)


def _load_state() -> dict:
    default = {"last_update_id": 0, "pending_approvals": []}
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            for k, v in default.items():
                data.setdefault(k, v)
            return data
    except (json.JSONDecodeError, OSError) as e:
        log.error("[TELEGRAM] State load error: %s", e)
    return default


def _save_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2, default=str))
    except OSError as e:
        log.error("[TELEGRAM] State save error: %s", e)


def _is_configured() -> bool:
    return bool(_TELEGRAM_BOT_TOKEN and _TELEGRAM_CEO_CHAT_ID)


def _is_from_alexis(update: dict) -> bool:
    """Return True if this update is driven by Alexis.

    Acceptance paths — both private DM with @MAXIA_AI_bot and the
    @MAXIA_alerts channel route to CEO handlers:

    1. Callback_query: ``from.id`` = Alexis's user_id (the most reliable
       signal — his id is unique across all chats).
    2. Text message (DM): ``from.id`` = Alexis or ``chat.id`` = CEO chat.
    3. Channel_post (Alexis typing as channel admin): ``from.id`` = Alexis
       or ``chat.id`` = @MAXIA_alerts (both set).
    """
    cb = update.get("callback_query")
    if cb:
        from_id = str(cb.get("from", {}).get("id", ""))
        if _TELEGRAM_ALEXIS_USER_ID and from_id == str(_TELEGRAM_ALEXIS_USER_ID):
            return True
        cb_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
        if _TELEGRAM_CEO_CHAT_ID and cb_chat_id == str(_TELEGRAM_CEO_CHAT_ID):
            return True
        return False

    # message OR channel_post — same shape downstream
    message = update.get("message") or update.get("channel_post") or {}
    from_id = str(message.get("from", {}).get("id", ""))
    if _TELEGRAM_ALEXIS_USER_ID and from_id == str(_TELEGRAM_ALEXIS_USER_ID):
        return True
    chat_id = str(message.get("chat", {}).get("id", ""))
    if _TELEGRAM_CEO_CHAT_ID and chat_id == str(_TELEGRAM_CEO_CHAT_ID):
        return True
    return False


async def _telegram_request(method: str, data: Optional[dict] = None) -> Optional[dict]:
    url = f"{_TELEGRAM_API}{_TELEGRAM_BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 10) as client:
            resp = await client.post(url, json=data) if data else await client.get(url)
            resp.raise_for_status()
            result = resp.json()
            if result.get("ok"):
                return result.get("result")
            log.warning("[TELEGRAM] API error: %s", result.get("description", "unknown"))
    except httpx.TimeoutException:
        pass
    except Exception as e:
        log.error("[TELEGRAM] Request error (%s): %s", method, e)
    return None


async def _send_message(
    text: str,
    reply_markup: Optional[dict] = None,
    chat_id: Optional[str] = None,
) -> Optional[dict]:
    """Send a Markdown message. Defaults to the CEO channel/destination.

    Pass an explicit chat_id when replying in-thread to Alexis from his
    own DM — otherwise the reply lands in the @MAXIA_alerts channel and
    Alexis never sees it where he sent the command.
    """
    target = str(chat_id) if chat_id is not None else _TELEGRAM_CEO_CHAT_ID
    data: dict = {"chat_id": target, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return await _telegram_request("sendMessage", data)


async def _send_message_to(chat_id: int, text: str) -> Optional[dict]:
    """Send a plain-text message to ANY chat (used for prospect replies).

    No Markdown to avoid parse errors on user-generated identifiers.
    """
    data: dict = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    return await _telegram_request("sendMessage", data)


def _check_rate_limit(from_id: int) -> bool:
    """Sliding-window rate limit per from.id. Returns True if within budget."""
    now = time.time()
    window = _prospect_msg_log[from_id]
    cutoff = now - 3600
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= PROSPECT_RATE_LIMIT_PER_HOUR:
        return False
    window.append(now)
    return True


def _is_sensitive_telegram(text: str) -> bool:
    """Reuse the multilingual escalation keyword check from vps_bridge."""
    try:
        from missions.vps_bridge import _is_sensitive
        return _is_sensitive(text)
    except Exception:
        return False


async def _handle_prospect_message(
    *,
    chat_id: int,
    from_id: int,
    text: str,
    language_code: Optional[str],
    mem: dict,
) -> None:
    """Route a non-Alexis Telegram message through MaxiaSalesAgent and reply.

    Side effects:
        - Persists conversation in ``sales/conversations.db`` via the agent
        - Sends the reply BACK to the prospect's chat
        - Alerts Alexis on his own chat if the conversation reaches closing
        - Logs the prospect message in ``mem["telegram_prospect_messages"]``
    """
    if not _ENABLE_PROSPECTS:
        return

    # 1. Sensitive keyword escalation — never let the LLM near these
    if _is_sensitive_telegram(text):
        log.info("[TELEGRAM] sensitive escalation from chat=%s", chat_id)
        await _send_message_to(
            chat_id,
            "Alexis will follow up with you personally on this. "
            "Please allow up to 24 hours for a response."
        )
        try:
            from notifier import notify_telegram
            await notify_telegram(
                "Sales escalation",
                f"Sensitive keyword detected from prospect chat={chat_id} from={from_id}.\n\n"
                f"Message: {text[:300]}\n\nIntervene on @MAXIA_AI_bot."
            )
        except Exception:
            pass
        return

    # 2. Rate limit per from.id
    if not _check_rate_limit(from_id):
        log.info("[TELEGRAM] rate limit hit for from=%s", from_id)
        await _send_message_to(
            chat_id,
            "You're sending messages too fast. Please wait a bit and try again."
        )
        return

    # 3. Route through smart_reply -> MaxiaSalesAgent
    try:
        from missions.telegram_smart_reply import answer_user_message
        reply = await answer_user_message(
            user_message=text,
            history=[],
            language_code=language_code,
            user_id=str(from_id),
            channel="telegram",
        )
    except Exception as e:
        log.warning("[TELEGRAM] smart_reply failed for prospect: %s", e)
        await _send_message_to(
            chat_id,
            "I had trouble generating a reply just now. "
            "Please try again or visit https://maxiaworld.app"
        )
        return

    if not isinstance(reply, str) or len(reply) < 5:
        await _send_message_to(
            chat_id,
            "I could not produce an answer to that. Please rephrase or visit https://maxiaworld.app"
        )
        return

    # 4. Send the reply back to the prospect's chat
    await _send_message_to(chat_id, reply)

    # 5. Log in memory for the weekly report
    mem.setdefault("telegram_prospect_messages", []).append({
        "date": datetime.now().isoformat(),
        "chat_id": chat_id,
        "from_id": from_id,
        "lang": language_code or "?",
        "in": text[:200],
        "out": reply[:200],
    })
    if len(mem["telegram_prospect_messages"]) > 500:
        mem["telegram_prospect_messages"] = mem["telegram_prospect_messages"][-500:]


async def _process_one_update(
    update: dict,
    mem: dict,
    actions: Optional[dict] = None,
) -> int:
    """Route a single Telegram update. Returns 1 if processed, 0 otherwise.

    Called by the shared telegram_router for every incoming update.
    Ordering:

      1. ``callback_query`` — router already resolved explicit waiters
         before calling us, so this branch only runs for legacy
         action_ids registered via telegram_chat.request_approval.
      2. Public slash commands (``/start``, ``/price``, ``/help``) —
         handled for everyone so prospects and Alexis both see the
         Mini App welcome when they type /start.
      3. CEO commands from Alexis (``/status``, ``/metrics``) or
         free-text chat (routed to LLM) — reply goes back to the chat
         Alexis sent from, not the default CEO channel.
      4. Prospect text — routed to MaxiaSalesAgent.
    """
    # 1. Callback queries — legacy fallback only (router resolved explicit waiters).
    if "callback_query" in update:
        cb = update["callback_query"]
        state = _load_state()
        await _handle_callback_query(
            callback_data=cb.get("data", ""),
            callback_query_id=cb.get("id", ""),
            state=state,
        )
        _save_state(state)
        return 1

    # 2. Extract text + identity. Accept "message" (DM) or "channel_post"
    #    (Alexis typing as admin in @MAXIA_alerts), same shape.
    message = update.get("message") or update.get("channel_post") or {}
    # Skip the bot's own channel posts to avoid self-loops if Telegram
    # ever decides to echo them back (currently it does not, but the
    # filter is cheap insurance).
    sender_raw = message.get("from", {}) or {}
    if sender_raw.get("is_bot"):
        return 0
    text = (message.get("text") or "").strip()
    chat = message.get("chat", {}) or {}
    sender = sender_raw
    chat_id = chat.get("id")
    from_id = sender.get("id")
    lang_code = sender.get("language_code") or "fr"

    if not text or chat_id is None:
        return 0

    # 3. Public slash commands — available to everyone (any DM with @MAXIA_AI_bot).
    first_word = text.split(None, 1)[0].lower() if text else ""
    if first_word in {"/start", "/price", "/help"}:
        try:
            from telegram_public import handle_public_command
            handled = await handle_public_command(
                command=first_word,
                text=text,
                chat_id=chat_id,
                first_name=sender.get("first_name", "") or "",
                lang_code=lang_code,
            )
            if handled:
                return 1
        except Exception as e:
            log.warning("[TELEGRAM] public command %s error: %s", first_word, e)

    # 4. Alexis CEO commands + free-text
    if _is_from_alexis(update):
        if first_word == "/status":
            tweet_count = len(mem.get("tweets_posted", []))
            alerts = len(mem.get("health_alerts", []))
            outreach = len(mem.get("outreach_sent", []))
            status_text = (
                f"*MAXIA CEO Status*\n\n"
                f"Tweets postes: {tweet_count}\n"
                f"Alertes sante: {alerts}\n"
                f"Emails envoyes: {outreach}\n"
                f"Dernier check: {datetime.now().strftime('%H:%M')}"
            )
            await _send_message(status_text, chat_id=chat_id)
        elif first_word == "/metrics":
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{VPS_URL}/health")
                    if resp.status_code == 200:
                        health = resp.json()
                        await _send_message(
                            f"*VPS Health:*\n```\n{json.dumps(health, indent=2)[:500]}\n```",
                            chat_id=chat_id,
                        )
                    else:
                        await _send_message(
                            f"VPS Health: HTTP {resp.status_code}",
                            chat_id=chat_id,
                        )
            except Exception as e:
                await _send_message(f"Erreur metrics: {str(e)[:100]}", chat_id=chat_id)
        else:
            response = await _handle_text_message(
                text, mem, language_code=lang_code, actions_today=actions,
            )
            await _send_message(response, chat_id=chat_id)

        mem.setdefault("telegram_messages", []).append({
            "date": datetime.now().isoformat(),
            "from": "alexis",
            "text": text[:200],
        })
        if len(mem.get("telegram_messages", [])) > 200:
            mem["telegram_messages"] = mem["telegram_messages"][-200:]
        return 1

    # 5. Prospect text — route to MaxiaSalesAgent
    if from_id is not None:
        try:
            await _handle_prospect_message(
                chat_id=chat_id,
                from_id=from_id,
                text=text,
                language_code=lang_code,
                mem=mem,
            )
            return 1
        except Exception as e:
            log.warning("[TELEGRAM] prospect handler error: %s", e)
    return 0


async def handle_update(update: dict, mem: dict, actions: dict) -> None:
    """Router entry point. Thin wrapper around _process_one_update.

    Registered with telegram_router via register_message_handler(). The
    ``actions`` argument is accepted for handler-signature compatibility
    but currently unused.
    """
    if not _is_configured():
        return
    try:
        await _process_one_update(update, mem, actions=actions)
    except Exception as e:
        log.warning("[TELEGRAM] handle_update error: %s", e)


async def _handle_text_message(
    text: str,
    mem: dict,
    language_code: str = "fr",
    actions_today: Optional[dict] = None,
) -> str:
    """Generate a knowledge-grounded response via the V9 smart-reply layer.

    Falls back to the legacy Chat agent prompt if the smart-reply module
    is unavailable for any reason. ``mem`` and ``actions_today`` are
    forwarded to smart_reply so the RUNTIME_STATE block can ground
    "what did you do" questions in real data (counters, recent mission
    history, SQLite action log).
    """
    # Reload actions_today fresh from disk — the router's copy is captured
    # at boot and goes stale across day boundaries. Reading on demand
    # guarantees the chat always sees today's real counters.
    try:
        from memory import load_actions_today
        actions_today = load_actions_today()
    except Exception as _e:
        log.debug("[TELEGRAM] load_actions_today failed: %s", _e)

    # Build conversation history from mem. Cap at last 6 turns: longer
    # history + the RUNTIME_STATE block + KNOWLEDGE overflow qwen3's
    # 8192 ctx window and cause empty/hallucinated replies. Full log is
    # still persisted below; we just don't ship it all to the LLM.
    full_history = mem.get("telegram_conversation", []) or []
    history = [
        {"role": h.get("role", "user"), "content": (h.get("content", "") or "")[:400]}
        for h in full_history[-6:]
    ]

    try:
        from missions.telegram_smart_reply import answer_user_message
        response = await answer_user_message(
            user_message=text,
            history=history,
            language_code=language_code,
            mem=mem,
            actions_today=actions_today,
        )
    except Exception as e:
        log.warning("[TELEGRAM] smart_reply failed, falling back: %s", e)
        # Legacy fallback path
        parts = []
        for key, label in [("disboard_bumps", "DisboardBumps"),
                           ("github_prospects", "Prospects"),
                           ("health_alerts", "Alerts")]:
            items = mem.get(key, [])[-3:]
            if items:
                parts.append(f"{label}: {len(items)} recent")
        context = "\n".join(parts) if parts else "No recent activity."
        prompt = (
            f"Alexis wrote on Telegram: {text}\n\n"
            f"Recent CEO activity:\n{context}\n\n"
            f"Reply concisely in French."
        )
        response = await ask(CHAT, prompt, knowledge=MAXIA_KNOWLEDGE[:2000])

    if not response:
        response = "Je suis la, mais je n'ai pas pu generer de reponse. Reessaie."

    # Persist conversation history (rolling window of 20 turns)
    history = mem.setdefault("telegram_conversation", [])
    history.append({"role": "user", "content": text[:1000]})
    history.append({"role": "assistant", "content": response[:1000]})
    if len(history) > 20:
        mem["telegram_conversation"] = history[-20:]

    return response


async def _handle_callback_query(callback_data: str, callback_query_id: str, state: dict) -> None:
    """Handle GO/NO button presses for approval flows."""
    await _telegram_request("answerCallbackQuery", {"callback_query_id": callback_query_id})

    parts = callback_data.split(":", 1)
    if len(parts) != 2:
        await _send_message("Format de callback invalide.")
        return

    action_type, action_id = parts[0], parts[1]
    pending = state.get("pending_approvals", [])
    matched = [p for p in pending if p.get("id") == action_id]
    if not matched:
        await _send_message(f"Action `{action_id}` non trouvee ou deja traitee.")
        return

    approved = action_type == "approve"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{VPS_URL}/api/ceo/approval-result",
                json={"action_id": action_id, "approved": approved},
            )
            label = "approuvee" if approved else "rejetee"
            name = matched[0].get("name", action_id)
            if approved and resp.status_code != 200:
                await _send_message(f"Erreur VPS: HTTP {resp.status_code}")
            else:
                await _send_message(f"Action `{name}` {label}.")
    except Exception as e:
        await _send_message(f"Erreur: {str(e)[:100]}")

    state["pending_approvals"] = [p for p in pending if p.get("id") != action_id]


async def request_approval(action_name: str, action_id: str, description: str, level: str = "ORANGE") -> None:
    """Send an approval request to Alexis via Telegram with GO/NO buttons."""
    if not _is_configured():
        log.warning("[TELEGRAM] Not configured — cannot request approval")
        return
    text = (f"[{level}] Approbation requise\n\n*Action:* {action_name}\n"
            f"*Description:* {description}\n*Niveau:* {level}\n\nApprouver cette action?")
    reply_markup = {"inline_keyboard": [[
        {"text": "GO", "callback_data": f"approve:{action_id}"},
        {"text": "NO", "callback_data": f"reject:{action_id}"},
    ]]}
    await _send_message(text, reply_markup)

    state = _load_state()
    state["pending_approvals"].append({
        "id": action_id, "name": action_name, "description": description,
        "level": level, "requested_at": datetime.now().isoformat(),
    })
    cutoff = datetime.fromtimestamp(time.time() - 86400).isoformat()
    state["pending_approvals"] = [p for p in state["pending_approvals"] if p.get("requested_at", "") >= cutoff]
    _save_state(state)


# mission_telegram_chat removed: the shared telegram_router is now the
# single Telegram poller. ceo_main.py starts the router at boot and
# registers handle_update() above as the message handler. Polling is
# handled exclusively by local_ceo/telegram_router.py.
