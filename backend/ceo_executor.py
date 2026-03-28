"""CEO Executor — Routes CEO decisions to real sub-agent actions.

Safety rules:
  VERT   -> auto-execute immediately
  ORANGE -> max 1/day per cible, log warning (high cost = queue for approval)
  ROUGE  -> NEVER auto-execute, queue for dashboard approval + Discord alert
"""
import asyncio, json, re, time, uuid
from datetime import date, datetime

# ══════════════════════════════════════════
# Daily execution tracker (resets each day)
# ══════════════════════════════════════════

_daily_counts: dict = {}  # {cible: count}
_daily_date: str = ""

MAX_TWEETS_DAY = 10
MAX_PROSPECTS_DAY = 10


def _reset_if_new_day():
    global _daily_counts, _daily_date
    today = datetime.utcnow().strftime("%Y-%m-%d")  # UTC pour coherence cross-timezone
    if _daily_date != today:
        _daily_counts = {}
        _daily_date = today


def _orange_limit_reached(cible: str) -> bool:
    """ORANGE decisions: max 1 per day per cible."""
    _reset_if_new_day()
    return _daily_counts.get(cible, 0) >= 1


def _increment_count(cible: str):
    _reset_if_new_day()
    _daily_counts[cible] = _daily_counts.get(cible, 0) + 1


# ══════════════════════════════════════════
# Discord alert for ROUGE decisions
# ══════════════════════════════════════════

async def _log_and_alert(decision: dict, memory):
    """Log ROUGE decision and send Discord alert."""
    action = decision.get("action", "")
    cible = decision.get("cible", "")
    try:
        from ceo_maxia import alert_rouge
        await alert_rouge(
            f"ROUGE decision for {cible}",
            f"Action: {action[:300]}\nRequires manual approval from founder.",
            deadline_h=2,
        )
    except Exception as e:
        print(f"[CEO-Executor] Discord alert failed: {e}")
    memory.log_decision("ROUGE", action, "BLOCKED — requires founder approval", cible)


def _queue_pending(decision: dict, memory):
    """Ajoute une decision en attente d'approbation dans la memoire CEO."""
    pending = memory._data.setdefault("pending_approvals", [])
    pending.append({
        "id": str(uuid.uuid4())[:8],
        "ts": datetime.utcnow().isoformat(),
        "action": decision.get("action", "")[:300],
        "cible": decision.get("cible", ""),
        "priorite": str(decision.get("priorite", "ORANGE")).upper(),
        "raison": decision.get("raison", ""),
        "status": "pending",
    })
    # Garder max 50 pending
    memory._data["pending_approvals"] = [p for p in pending if p["status"] == "pending"][-50:]
    memory.save()


# ══════════════════════════════════════════
# Main dispatcher
# ══════════════════════════════════════════

async def execute_decision(decision: dict, memory, db=None) -> dict:
    """Route a CEO decision to the appropriate executor.

    Returns dict with keys: executed (bool), reason/detail (str).
    """
    priorite = str(decision.get("priorite", "ORANGE")).upper()
    action = str(decision.get("action", ""))
    cible = str(decision.get("cible", "")).upper()

    # ROUGE = never auto-execute, queue for dashboard approval
    if priorite == "ROUGE":
        await _log_and_alert(decision, memory)
        _queue_pending(decision, memory)
        return {"executed": False, "reason": "ROUGE — requires manual approval"}

    # ORANGE with high cost = queue for approval too
    if priorite == "ORANGE" and decision.get("cost_usd", 0) > 5:
        _queue_pending(decision, memory)
        return {"executed": False, "reason": "ORANGE high cost — queued for approval"}

    # ORANGE = max 1/day per cible
    if priorite == "ORANGE":
        if _orange_limit_reached(cible):
            print(f"[CEO-Executor] ORANGE limit reached for {cible}, skipping")
            return {"executed": False, "reason": f"ORANGE limit reached for {cible} today"}

    # Route to executor based on cible
    try:
        result = await _route(cible, action, decision, memory, db)
        # Track orange executions
        if priorite == "ORANGE":
            _increment_count(cible)
        # Auto-re-enable: si l'agent etait auto-disabled et que l'action reussit
        if result.get("executed"):
            # Auto-re-enable if agent was auto-disabled
            if memory.is_agent_disabled(cible):
                disabled_info = memory._data.get("disabled_agents", {}).get(cible, {})
                if "Auto-disabled" in disabled_info.get("reason", ""):
                    memory.enable_agent(cible)
                    print(f"[CEO-Executor] AUTO-RE-ENABLE: {cible} (action succeeded)")
            # Reset error count for this agent on success
            agent_error_map = {"GHOST-WRITER": "ceo_executor_tweet", "HUNTER": "ceo_executor_prospect",
                               "DEPLOYER": "ceo_executor_blog", "SCOUT": "ceo_executor_scout"}
            err_source = agent_error_map.get(cible)
            if err_source:
                for err in memory._data.get("erreurs_recurrentes", []):
                    if err["source"] == err_source and err.get("count", 0) > 0:
                        err["count"] = 0
                        err["auto_disabled"] = False
        return result
    except Exception as e:
        error_msg = f"Execution error for {cible}: {e}"
        print(f"[CEO-Executor] {error_msg}")
        memory.log_error("ceo_executor", error_msg)
        return {"executed": False, "reason": error_msg}


async def _route(cible: str, action: str, decision: dict, memory, db=None) -> dict:
    """Route to the right executor function based on cible."""
    action_lower = action.lower()

    if cible == "GHOST-WRITER":
        # Determine if it's a tweet, blog, or other content
        if any(kw in action_lower for kw in ["tweet", "twitter", "post tweet", "thread"]):
            text = _extract_quoted_text(action) or _extract_content_after(action, ["tweet", "poster", "post"])
            if text:
                return await execute_tweet(text, memory)
            return {"executed": False, "reason": "Could not extract tweet text from action"}

        if any(kw in action_lower for kw in ["blog", "article", "deploy blog"]):
            title = _extract_quoted_text(action) or "MAXIA Update"
            return await execute_blog_deploy(title, action, memory)

        # Generic content request — log it, let GHOST-WRITER pick it up
        memory.update_agent("GHOST-WRITER", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"GHOST-WRITER tasked: {action[:80]}"}

    elif cible == "HUNTER":
        if any(kw in action_lower for kw in ["switch", "changer canal", "change canal"]):
            new_canal = _extract_canal(action)
            return await execute_hunter_switch(new_canal, memory)

        if any(kw in action_lower for kw in ["contact", "prospect", "outreach", "memo"]):
            wallet = _extract_wallet(action)
            message = _extract_quoted_text(action) or action
            canal = memory._data.get("hunter_canal", "solana_memo")
            return await execute_prospect_contact(wallet, message, canal, memory)

        # Generic hunter task
        memory.update_agent("HUNTER", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"HUNTER tasked: {action[:80]}"}

    elif cible == "WATCHDOG":
        # WATCHDOG tasks are monitoring — log them
        memory.update_agent("WATCHDOG", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"WATCHDOG tasked: {action[:80]}"}

    elif cible == "SOL-TREASURY":
        if any(kw in action_lower for kw in ["prix", "price", "commission", "fee", "adjust"]):
            return await execute_price_adjustment(None, action, memory, db)

        if any(kw in action_lower for kw in ["budget", "decay", "update budget"]):
            memory.update_agent("SOL-TREASURY", {"pending_action": action[:300], "from": "CEO"})
            memory.save()
            return {"executed": True, "detail": f"SOL-TREASURY tasked: {action[:80]}"}

        memory.update_agent("SOL-TREASURY", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"SOL-TREASURY tasked: {action[:80]}"}

    elif cible == "RESPONDER":
        memory.update_agent("RESPONDER", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"RESPONDER tasked: {action[:80]}"}

    elif cible == "RADAR":
        memory.update_agent("RADAR", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"RADAR tasked: {action[:80]}"}

    elif cible == "TESTIMONIAL":
        memory.update_agent("TESTIMONIAL", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"TESTIMONIAL tasked: {action[:80]}"}

    elif cible == "SCOUT":
        if any(kw in action_lower for kw in ["scan", "search", "find", "discover"]):
            return await execute_scout_scan(action, memory)
        memory.update_agent("SCOUT", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"SCOUT tasked: {action[:80]}"}

    elif cible == "DEPLOYER":
        if any(kw in action_lower for kw in ["deploy", "blog", "github"]):
            return await execute_blog_deploy("MAXIA Deploy", action, memory)
        memory.update_agent("DEPLOYER", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"DEPLOYER tasked: {action[:80]}"}

    elif cible == "NEGOTIATOR":
        memory.update_agent("NEGOTIATOR", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"NEGOTIATOR tasked: {action[:80]}"}

    elif cible == "COMPLIANCE":
        # Check wallet si action contient une adresse
        wallet = _extract_wallet(action)
        if wallet:
            try:
                from ceo_maxia import compliance_check_wallet
                result = await compliance_check_wallet(wallet, memory)
                return {"executed": True, "detail": f"COMPLIANCE check: {wallet[:16]}... risk={result.get('risk', '?')}"}
            except Exception as e:
                return {"executed": False, "reason": f"COMPLIANCE error: {e}"}
        memory.update_agent("COMPLIANCE", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"COMPLIANCE tasked: {action[:80]}"}

    elif cible == "PARTNERSHIP":
        if any(kw in action_lower for kw in ["contact", "outreach", "reach out", "pitch"]):
            try:
                from ceo_maxia import partnership_outreach
                # Extraire le nom du partenaire
                partner = _extract_quoted_text(action) or action.split()[-1]
                result = await partnership_outreach(partner, "general", action, memory)
                return {"executed": True, "detail": f"PARTNERSHIP outreach: {partner}"}
            except Exception as e:
                return {"executed": False, "reason": f"PARTNERSHIP error: {e}"}
        memory.update_agent("PARTNERSHIP", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"PARTNERSHIP tasked: {action[:80]}"}

    elif cible == "ANALYTICS":
        memory.update_agent("ANALYTICS", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"ANALYTICS tasked: {action[:80]}"}

    elif cible == "CRISIS-MANAGER":
        memory.update_agent("CRISIS-MANAGER", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"CRISIS-MANAGER tasked: {action[:80]}"}

    elif cible == "MICRO":
        # MICRO wallet — petty cash pour experimentations
        memory.update_agent("MICRO", {"pending_action": action[:300], "from": "CEO"})
        memory.save()
        return {"executed": True, "detail": f"MICRO tasked: {action[:80]}"}

    elif cible == "FONDATEUR":
        # Never auto-execute for founder — always alert
        try:
            from ceo_maxia import alert_rouge
            await alert_rouge(f"Decision pour FONDATEUR", action[:500], deadline_h=4)
        except Exception:
            pass
        return {"executed": False, "reason": "FONDATEUR — routed as alert only"}

    else:
        # Unknown cible — log it (mais ne pas spammer les alertes)
        print(f"[CEO-Executor] Unknown cible: {cible}")
        return {"executed": False, "reason": f"Unknown cible: {cible}"}


# ══════════════════════════════════════════
# Individual executors
# ══════════════════════════════════════════

async def execute_tweet(text: str, memory) -> dict:
    """Post a tweet via twitter_bot. Returns result dict."""
    import uuid as _uuid
    action_id = f"tweet_{_uuid.uuid4().hex[:8]}"
    try:
        from twitter_bot import post_tweet
        result = await post_tweet(text)
        if result.get("success"):
            memory.log_decision("VERT", f"Tweet posted: {text[:80]}", "auto-executed", "GHOST-WRITER")
            memory.log_action_with_tracking("GHOST-WRITER", "tweet", action_id, text[:100])
            return {"executed": True, "detail": f"Tweet posted (id:{result.get('tweet_id', '?')})", "action_id": action_id}
        else:
            error = result.get("error", "unknown error")
            memory.log_error("ceo_executor_tweet", error)
            return {"executed": False, "reason": f"Tweet failed: {error}"}
    except Exception as e:
        memory.log_error("ceo_executor_tweet", str(e))
        return {"executed": False, "reason": f"Tweet error: {e}"}


async def execute_prospect_contact(wallet: str, message: str, canal: str, memory) -> dict:
    """Contact a prospect wallet via memo transfer or growth agent."""
    if not wallet:
        return {"executed": False, "reason": "No wallet address found in action"}

    # Check financial limits
    try:
        from security import check_financial_limits
        check = check_financial_limits(0.01)  # memo transfer costs ~0.001 SOL ~ $0.15
        if not check.get("allowed"):
            return {"executed": False, "reason": f"Financial limit: {check.get('reason', '')}"}
    except Exception as e:
        print(f"[CEO-Executor] Security check skipped: {e}")

    import uuid as _uuid
    action_id = f"prospect_{_uuid.uuid4().hex[:8]}"
    try:
        from solana_tx import send_memo_transfer
        memo_text = message[:400] if message else f"Check out MAXIA — AI Marketplace on Solana. maxiaworld.app"
        result = await send_memo_transfer(wallet, 0.001, memo_text)
        if result.get("success"):
            memory.hunter_contact(converted=False)
            memory.log_decision("VERT", f"Prospect contacted: {wallet[:16]}...", "auto-executed", "HUNTER")
            memory.log_action_with_tracking("HUNTER", "prospect", action_id, f"wallet={wallet[:16]}")
            return {"executed": True, "detail": f"Memo sent to {wallet[:16]}...", "action_id": action_id}
        else:
            error = result.get("error", "unknown")
            memory.log_error("ceo_executor_prospect", error)
            return {"executed": False, "reason": f"Prospect contact failed: {error}"}
    except Exception as e:
        memory.log_error("ceo_executor_prospect", str(e))
        return {"executed": False, "reason": f"Prospect error: {e}"}


async def execute_price_adjustment(service_id, adjustment_info: str, memory, db=None) -> dict:
    """Adjust a service price or commission tier via dynamic_pricing or direct DB."""
    try:
        from dynamic_pricing import adjust_market_fees
        if db is not None:
            result = await adjust_market_fees(db)
            memory.log_decision("VERT", f"Price adjustment executed: {adjustment_info[:80]}", "auto-executed", "SOL-TREASURY")
            return {"executed": True, "detail": f"Price adjusted: {json.dumps(result)[:100]}"}
        else:
            # No DB available — queue it
            memory.update_agent("SOL-TREASURY", {"pending_price_adjust": adjustment_info[:300]})
            memory.save()
            return {"executed": True, "detail": f"Price adjustment queued (no DB): {adjustment_info[:80]}"}
    except Exception as e:
        memory.log_error("ceo_executor_price", str(e))
        return {"executed": False, "reason": f"Price adjustment error: {e}"}


async def execute_blog_deploy(title: str, content: str, memory) -> dict:
    """Deploy a blog post via GitHub Pages or local fallback. Offloaded to task queue."""
    # Try to offload to task queue for non-blocking execution
    try:
        from ceo_maxia import task_queue
        await task_queue.put(f"blog_{title[:20]}", _do_blog_deploy, title, content, memory)
        return {"executed": True, "detail": f"Blog queued: {title[:60]}"}
    except Exception:
        pass
    # Fallback: direct execution
    return await _do_blog_deploy(title, content, memory)


async def _do_blog_deploy(title: str, content: str, memory) -> dict:
    """Actual blog deploy implementation."""
    import os
    blog_dir = os.path.join(os.path.dirname(__file__), "..", "blog")
    try:
        os.makedirs(blog_dir, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
        filename = f"{date.today().isoformat()}-{slug}.md"
        filepath = os.path.join(blog_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n")
            f.write(f"*Published {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} by CEO MAXIA*\n\n")
            f.write(content)

        import uuid as _uuid
        action_id = f"blog_{_uuid.uuid4().hex[:8]}"
        memory.log_decision("VERT", f"Blog deployed: {title}", "auto-executed", "DEPLOYER")
        memory.log_action_with_tracking("DEPLOYER", "blog", action_id, title[:100])
        print(f"[CEO-Executor] Blog saved: {filepath}")
        return {"executed": True, "detail": f"Blog saved: {filename}", "action_id": action_id}
    except Exception as e:
        memory.log_error("ceo_executor_blog", str(e))
        return {"executed": False, "reason": f"Blog deploy error: {e}"}


async def execute_hunter_switch(new_canal: str, memory) -> dict:
    """Switch the HUNTER agent to a different outreach channel."""
    if not new_canal:
        return {"executed": False, "reason": "No canal specified for switch"}

    valid_canals = ["solana_memo", "discord", "twitter", "reddit", "telegram", "github"]
    canal_clean = new_canal.lower().strip()
    if canal_clean not in valid_canals:
        # Try fuzzy match
        for vc in valid_canals:
            if vc in canal_clean or canal_clean in vc:
                canal_clean = vc
                break
        else:
            return {"executed": False, "reason": f"Unknown canal: {new_canal}. Valid: {valid_canals}"}

    old = memory.hunter_switch(canal_clean)
    memory.log_decision("VERT", f"HUNTER canal switched: {old} -> {canal_clean}", "auto-executed", "HUNTER")
    print(f"[CEO-Executor] HUNTER canal: {old} -> {canal_clean}")
    return {"executed": True, "detail": f"Canal switched: {old} -> {canal_clean}"}


# ══════════════════════════════════════════
# Text extraction helpers
# ══════════════════════════════════════════

def _extract_quoted_text(action: str) -> str:
    """Extract text between quotes from an action string."""
    # Try double quotes first, then single quotes
    for pattern in [r'"([^"]+)"', r"'([^']+)'"]:
        m = re.search(pattern, action)
        if m:
            return m.group(1)
    return ""


def _extract_content_after(action: str, keywords: list) -> str:
    """Extract content after a keyword in the action string."""
    action_lower = action.lower()
    for kw in keywords:
        idx = action_lower.find(kw)
        if idx >= 0:
            rest = action[idx + len(kw):].strip(" :->")
            if rest:
                return rest[:280]
    return ""


def _extract_wallet(action: str) -> str:
    """Extract a Solana wallet address (base58, 32-44 chars) from action."""
    m = re.search(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b', action)
    return m.group(0) if m else ""


def _extract_canal(action: str) -> str:
    """Extract canal name from an action string."""
    canals = ["solana_memo", "discord", "twitter", "reddit", "telegram", "github"]
    action_lower = action.lower()
    for c in canals:
        if c in action_lower:
            return c
    # Check for partial matches
    if "memo" in action_lower:
        return "solana_memo"
    return ""


# ══════════════════════════════════════════
# SCOUT executor
# ══════════════════════════════════════════

async def execute_scout_scan(action: str, memory) -> dict:
    """Trigger a SCOUT scan on demand. Offloaded to task queue."""
    try:
        from ceo_maxia import task_queue
        await task_queue.put("scout_scan", _do_scout_scan, memory)
        return {"executed": True, "detail": "SCOUT scan queued"}
    except Exception:
        pass
    return await _do_scout_scan(memory)


async def _do_scout_scan(memory) -> dict:
    try:
        from scout_agent import scout_agent
        agents = await scout_agent.scan_all_chains()
        stats = scout_agent.get_stats()
        memory.update_agent("SCOUT", stats)
        memory.log_decision("VERT", f"SCOUT scan: {len(agents)} agents found", "auto-executed", "SCOUT")
        return {"executed": True, "detail": f"SCOUT scan done: {len(agents)} AI agents discovered"}
    except Exception as e:
        memory.log_error("ceo_executor_scout", str(e))
        return {"executed": False, "reason": f"SCOUT scan error: {e}"}


# ══════════════════════════════════════════
# CEO Local actions (PC -> VPS bridge)
# ══════════════════════════════════════════

async def execute_update_price(service_id, new_price: float, reason: str, memory, db=None) -> dict:
    """Update a service price directly in DB (called from CEO local via API)."""
    try:
        if db is not None:
            await db.execute(
                "UPDATE services SET price = ? WHERE id = ?",
                (new_price, service_id),
            )
            memory.log_decision("VERT", f"Price updated: service={service_id} -> ${new_price} ({reason})", "ceo-local", "SOL-TREASURY")
            return {"executed": True, "detail": f"Price updated: {service_id} -> ${new_price}"}
        return {"executed": False, "reason": "No DB available"}
    except Exception as e:
        return {"executed": False, "reason": f"Price update error: {e}"}


async def execute_generate_report(topic: str, memory) -> dict:
    """Generate a report using the LLM router."""
    try:
        from llm_router import router as llm_router, Tier
        from ceo_maxia import CEO_IDENTITY
        prompt = (
            f"Generate a concise report about: {topic}\n"
            f"Include: key metrics, trends, recommendations.\n"
            f"Format: markdown with headers and bullet points.\n"
            f"Max 500 words."
        )
        report = await llm_router.call(
            prompt, tier=Tier.LOCAL,
            system=CEO_IDENTITY + "\nMode ANALYTICS — rapport.",
            max_tokens=1500,
        )
        if report:
            memory.log_decision("VERT", f"Report generated: {topic}", "ceo-local", "ANALYTICS")
            return {"executed": True, "detail": f"Report generated: {topic[:50]}"}
        return {"executed": False, "reason": "Empty report"}
    except Exception as e:
        return {"executed": False, "reason": f"Report error: {e}"}


async def execute_send_alert(message: str, memory) -> dict:
    """Send a Discord alert."""
    try:
        from ceo_maxia import alert_info
        await alert_info(message)
        memory.log_decision("VERT", f"Alert sent: {message[:80]}", "ceo-local", "WATCHDOG")
        return {"executed": True, "detail": f"Alert sent: {message[:60]}"}
    except Exception as e:
        return {"executed": False, "reason": f"Alert error: {e}"}
