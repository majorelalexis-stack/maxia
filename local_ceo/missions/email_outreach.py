"""Mission — Email Outreach: send personalized cold emails to approved scout agents.

Takes agents approved by Alexis from scout_pending_contacts.json,
generates personalized cold emails via the Writer agent,
and sends via email_manager.send_outbound_prospect().

Limits: max 3 outbound emails/day. Each email is logged in memory.
"""
import json
import logging
import os
from datetime import datetime
from typing import Optional

from agents import WRITER, MAXIA_KNOWLEDGE
from llm import ask
from scheduler import send_mail

log = logging.getLogger("ceo")

_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(__file__))  # local_ceo/
_PENDING_FILE = os.path.join(_LOCAL_CEO_DIR, "scout_pending_contacts.json")
_MAX_OUTREACH_PER_DAY = 3


def _load_pending_contacts() -> list[dict]:
    """Load approved contacts pending outreach."""
    try:
        if os.path.exists(_PENDING_FILE):
            with open(_PENDING_FILE, "r", encoding="utf-8") as f:
                contacts = json.loads(f.read())
            if isinstance(contacts, list):
                return contacts
    except (json.JSONDecodeError, OSError) as e:
        log.error("[OUTREACH] Load pending contacts error: %s", e)
    return []


def _save_pending_contacts(contacts: list[dict]) -> None:
    """Save updated pending contacts list."""
    try:
        with open(_PENDING_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(contacts, indent=2, default=str, ensure_ascii=False))
    except OSError as e:
        log.error("[OUTREACH] Save pending contacts error: %s", e)


def _count_outreach_today(mem: dict) -> int:
    """Count how many outreach emails were sent today."""
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(
        1 for entry in mem.get("outreach_sent", [])
        if entry.get("date", "").startswith(today)
    )


async def _generate_cold_email(
    name: str,
    email: str,
    context: str,
) -> Optional[dict]:
    """Generate a personalized cold email using the Writer agent.

    Returns dict with 'subject' and 'body', or None on failure.
    """
    prompt = (
        f"Write a SHORT cold email to {name} about MAXIA.\n\n"
        f"Context about them: {context[:500]}\n\n"
        f"Rules:\n"
        f"- Subject line: max 8 words, personalized to their project\n"
        f"- Body: max 150 words, explain what MAXIA can do for THEM specifically\n"
        f"- MAXIA: AI-to-AI marketplace on 15 blockchains, USDC payments, "
        f"65 tokens swap, GPU rental, 46 MCP tools, on-chain escrow\n"
        f"- Include a clear CTA (visit maxiaworld.app, reply to discuss, etc.)\n"
        f"- Professional, not salesy, developer-friendly tone\n"
        f"- Sign as 'MAXIA Team'\n\n"
        f"Format: first line = subject (no prefix), rest = body."
    )

    response = await ask(WRITER, prompt, knowledge=MAXIA_KNOWLEDGE[:1500])
    if not response or len(response) < 30:
        log.warning("[OUTREACH] Writer generated insufficient email for %s", name)
        return None

    lines = response.strip().split("\n", 1)
    subject = lines[0].replace("Subject:", "").replace("subject:", "").strip().strip('"')
    body = lines[1].strip() if len(lines) > 1 else response

    # Sanity check: subject should be short, body should exist
    if len(subject) > 120:
        subject = subject[:117] + "..."
    if len(body) < 20:
        return None

    return {"subject": subject, "body": body}


async def mission_email_outreach(mem: dict, actions: dict) -> None:
    """Send personalized cold emails to approved scout contacts.

    Flow:
    1. Load pending contacts (approved by Alexis via scout mission)
    2. Filter to contacts with email addresses and 'approved' status
    3. Generate personalized email via Writer agent
    4. Send via email_manager.send_outbound_prospect()
    5. Log in memory, remove from pending
    """
    # Check daily limit
    sent_today = _count_outreach_today(mem)
    if sent_today >= _MAX_OUTREACH_PER_DAY:
        log.info("[OUTREACH] Daily limit reached (%d/%d) — skip", sent_today, _MAX_OUTREACH_PER_DAY)
        return

    # Check action counter
    if actions["counts"].get("outreach_sent", 0) >= _MAX_OUTREACH_PER_DAY:
        return

    # Load pending contacts
    contacts = _load_pending_contacts()
    if not contacts:
        log.debug("[OUTREACH] No pending contacts")
        return

    # Filter: approved + has email
    eligible = [
        c for c in contacts
        if c.get("status") == "approved"
        and c.get("email")
        and "@" in c.get("email", "")
    ]

    if not eligible:
        log.debug("[OUTREACH] No approved contacts with email addresses")
        return

    remaining = _MAX_OUTREACH_PER_DAY - sent_today
    to_send = eligible[:remaining]
    sent_count = 0

    for contact in to_send:
        name = contact.get("name", "there")
        email_addr = contact["email"]
        context = contact.get("context", contact.get("description", "AI agent developer"))

        # Generate personalized email
        email_content = await _generate_cold_email(name, email_addr, context)
        if not email_content:
            log.warning("[OUTREACH] Failed to generate email for %s", name)
            continue

        # Send via email_manager
        try:
            from email_manager import send_outbound_prospect
            result = await send_outbound_prospect(
                to=email_addr,
                name=name,
                context=context,
                llm_fn=_legacy_llm_wrapper,
            )

            if result.get("success"):
                sent_count += 1
                log.info("[OUTREACH] Email sent to %s (%s)", name, email_addr)

                # Log in memory
                mem.setdefault("outreach_sent", []).append({
                    "date": datetime.now().isoformat(),
                    "name": name,
                    "email": email_addr,
                    "subject": email_content["subject"][:80],
                    "status": "sent",
                })

                # Mark contact as contacted
                contact["status"] = "contacted"
                contact["contacted_date"] = datetime.now().isoformat()
                try:
                    from memory import log_action
                    log_action(
                        "email_outreach_sent",
                        target=email_addr,
                        details=f"to {name}: {email_content['subject'][:100]}",
                    )
                except Exception as _e:
                    log.debug("[OUTREACH] log_action failed: %s", _e)
                try:
                    from vector_memory_local import vmem as _vmem
                    if _vmem:
                        _vmem.store_contact(
                            username=name,
                            platform="email",
                            info=(
                                f"{email_addr}. Outreach "
                                f"{datetime.now().strftime('%Y-%m-%d')}: "
                                f"{email_content['subject'][:120]}"
                            ),
                        )
                except Exception as _e:
                    log.debug("[OUTREACH] store_contact failed: %s", _e)
            else:
                error = result.get("error", "unknown")
                log.warning("[OUTREACH] Send failed for %s: %s", name, error)
                # If daily limit hit on email_manager side, stop
                if "Limite" in str(error):
                    break
        except ImportError:
            log.error("[OUTREACH] email_manager not available")
            return
        except Exception as e:
            log.error("[OUTREACH] Error sending to %s: %s", name, e)

    # Save updated contacts (with 'contacted' status)
    if sent_count > 0:
        _save_pending_contacts(contacts)
        actions["counts"]["outreach_sent"] = actions["counts"].get("outreach_sent", 0) + sent_count

        # Trim memory list
        outreach_list = mem.get("outreach_sent", [])
        if len(outreach_list) > 200:
            mem["outreach_sent"] = outreach_list[-200:]

        # Notify Alexis
        await send_mail(
            f"[MAXIA CEO] Outreach — {sent_count} email(s) envoye(s)",
            f"Emails de prospection envoyes aujourd'hui: {sent_count}\n\n"
            + "\n".join(
                f"- {c.get('name', '?')} ({c.get('email', '?')})"
                for c in to_send[:sent_count]
            )
            + f"\n\nTotal aujourd'hui: {sent_today + sent_count}/{_MAX_OUTREACH_PER_DAY}",
        )

    log.info("[OUTREACH] Done — %d email(s) sent, %d eligible remaining",
             sent_count, len(eligible) - sent_count)


async def _legacy_llm_wrapper(prompt: str, max_tokens: int = 300) -> str:
    """Wrapper to adapt the legacy llm_fn signature for email_manager."""
    from llm import llm
    return await llm(prompt, max_tokens=max_tokens)
