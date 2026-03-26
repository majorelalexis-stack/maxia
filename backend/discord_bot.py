"""MAXIA Discord Bot V11 — Repond aux questions pertinentes (zero spam)

Regles :
- Ne poste JAMAIS de pub spontanee
- Repond uniquement quand quelqu'un pose une question pertinente
- Reponse utile d'abord, mention MAXIA a la fin
- Max 3 reponses/jour par serveur
- Art.1 sur tout le contenu
"""
import asyncio, time, re, json
import httpx
from config import DISCORD_BOT_TOKEN, GROQ_API_KEY, GROQ_MODEL, PORT, GPU_TIERS
_gpu_cheapest = f"${min(t['base_price_per_hour'] for t in GPU_TIERS if not t.get('local')):.2f}/h"

# Mots-cles surveilles (questions auxquelles le bot peut repondre)
KEYWORDS = {
    "gpu": {
        "triggers": ["gpu rental", "rent gpu", "louer gpu", "gpu cloud", "gpu cheap", "gpu pas cher", "runpod", "vast.ai", "gpu price"],
        "topic": "gpu",
    },
    "audit": {
        "triggers": ["smart contract audit", "audit smart contract", "security audit", "audit solidity", "audit rust", "vulnerability scan"],
        "topic": "audit",
    },
    "stocks": {
        "triggers": ["tokenized stocks", "xstocks", "buy apple stock", "buy tesla stock", "actions tokenisees", "tokenized equities", "stock on solana"],
        "topic": "stocks",
    },
    "ai_agent": {
        "triggers": ["ai agent", "agent ia", "ai marketplace", "ai service", "buy ai", "sell ai service", "agent autonome"],
        "topic": "ai",
    },
    "code": {
        "triggers": ["code generation", "generer du code", "ai code", "code review", "write code solana"],
        "topic": "code",
    },
}

# Reponses templates (utile d'abord, MAXIA a la fin)
RESPONSE_TEMPLATES = {
    "gpu": (
        "Pour la location de GPU cloud, voici les principaux fournisseurs :\n\n"
        "• **Vast.ai** — A partir de $0.34/h (RTX 4090), communautaire\n"
        f"• **RunPod** — {_gpu_cheapest} (RTX 4090), fiable\n"
        "• **Lambda Labs** — $1.29/h (A100), academique\n"
        f"• **MAXIA** — {_gpu_cheapest} (RTX 4090), prix coutant RunPod, paiement USDC sur Solana, commission 0.01% pour gros volumes (Whale)\n\n"
        "MAXIA est interessant si vous voulez payer en crypto sans compte bancaire. "
        "Details : `maxiaworld.app/api/public/gpu/tiers`"
    ),
    "audit": (
        "Pour l'audit de smart contracts, il y a plusieurs options :\n\n"
        "• **Audit humain** (Certik, Sherlock) — $5K-$250K, tres complet, 2-6 semaines\n"
        "• **Outils auto** (Slither, Mythril) — gratuit, detecte les patterns connus\n"
        "• **MAXIA AI Security Scan** — $4.99, scan IA rapide (LLaMA 3.3), resultat en secondes\n\n"
        "Le scan MAXIA ne remplace pas un audit humain complet, mais c'est utile comme pre-audit rapide avant de depenser $50K. "
        "Test : `maxiaworld.app/api/public/docs`"
    ),
    "stocks": (
        "Les actions tokenisees (xStocks) sur Solana sont disponibles via :\n\n"
        "• **Jupiter/Raydium** — DEX, 0% frais swap + slippage\n"
        "• **Kraken** — CEX, 0% avec USDG\n"
        "• **BingX** — CEX, spot + futures\n"
        "• **MAXIA** — Agregateur, commission 0.10% → 0.01% selon volume (swap), paiement USDC, API pour bots\n\n"
        "Actions dispo : Apple, Tesla, NVIDIA, Google, Microsoft, Amazon, Meta, S&P500, Nasdaq. "
        "Prix live : `maxiaworld.app/api/public/stocks`"
    ),
    "ai": (
        "Pour les services IA on-chain, quelques options :\n\n"
        "• **MAXIA** — Marketplace IA sur Solana : code gen ($1.99), audit ($4.99), data crypto ($1.99), traduction ($0.09). "
        "API publique gratuite pour agents IA. Inscription : `maxiaworld.app/api/public/register`\n"
        "• **Render Network** — GPU decentralise pour le rendu\n"
        "• **Akash** — Cloud decentralise general\n\n"
        "MAXIA est le seul a offrir une API unifiee (GPU + services IA + actions tokenisees) avec paiement USDC."
    ),
    "code": (
        "Pour la generation de code IA :\n\n"
        "• **ChatGPT/Claude** — $20/mois, illimite\n"
        "• **GitHub Copilot** — $10/mois, IDE integre\n"
        "• **MAXIA Code Engineer** — $1.99/tache, pay-per-use, pas d'abonnement, API pour bots\n\n"
        "MAXIA est utile si vous avez besoin de code ponctuellement sans abonnement mensuel. "
        "Supporte Python, Rust, JS, Solidity. `maxiaworld.app/api/public/docs`"
    ),
}

# Rate limiting par serveur
_server_responses: dict = {}  # server_id -> {date: count}
MAX_RESPONSES_PER_SERVER = 10

_running = False
_bot_user_id = ""  # Set at READY event


async def _ask_ceo(message: str, user: str = "discord_user") -> str:
    """Envoie un message au CEO MAXIA et retourne sa reponse."""
    try:
        import os
        admin_key = os.getenv("ADMIN_KEY", "")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"http://127.0.0.1:{PORT}/api/ceo/ask",
                json={"message": message},
                headers={"X-Admin-Key": admin_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("response", data.get("error", "Erreur CEO"))
            return f"Erreur API: {resp.status_code}"
    except Exception as e:
        return f"CEO indisponible: {e}"


async def _send_discord_message(channel_id: str, content: str):
    """Envoie un message sur Discord (split si > 2000 chars)."""
    if not DISCORD_BOT_TOKEN:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    # Discord limit: 2000 chars
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    async with httpx.AsyncClient(timeout=10) as client:
        for chunk in chunks:
            resp = await client.post(url, headers=headers, json={"content": chunk})
            if resp.status_code not in (200, 201):
                print(f"[DiscordBot] Erreur envoi {resp.status_code}: {resp.text[:100]}")


def _check_rate_limit(server_id: str) -> bool:
    """Verifie si on peut encore repondre dans ce serveur aujourd'hui."""
    today = time.strftime("%Y-%m-%d")
    key = f"{server_id}:{today}"
    _server_responses.setdefault(key, 0)
    if _server_responses[key] >= MAX_RESPONSES_PER_SERVER:
        return False
    return True


def _increment_rate(server_id: str):
    today = time.strftime("%Y-%m-%d")
    key = f"{server_id}:{today}"
    _server_responses.setdefault(key, 0)
    _server_responses[key] += 1


def _detect_topic(message_text: str) -> str:
    """Detecte si un message contient une question pertinente."""
    text_lower = message_text.lower()

    # Ignorer les messages trop courts
    if len(text_lower) < 15:
        return ""

    # Verifier que c'est une question (contient ? ou des mots interrogatifs)
    is_question = ("?" in text_lower or
                   any(w in text_lower for w in ["how", "where", "what", "comment", "ou", "quel", "which", "best", "meilleur", "recommend", "suggest"]))

    if not is_question:
        return ""

    # Chercher les mots-cles
    for category, config in KEYWORDS.items():
        for trigger in config["triggers"]:
            if trigger in text_lower:
                return config["topic"]

    return ""


async def respond_to_message(channel_id: str, topic: str):
    """Envoie une reponse utile sur Discord (templates mots-cles)."""
    response = RESPONSE_TEMPLATES.get(topic, "")
    if not response:
        return
    await _send_discord_message(channel_id, response)
    print(f"[DiscordBot] Repondu sur topic: {topic}")


async def run_discord_bot():
    """Boucle principale du bot Discord via Gateway WebSocket."""
    global _running, _bot_user_id
    _running = True

    if not DISCORD_BOT_TOKEN:
        print("[DiscordBot] Token absent — bot desactive")
        return

    print("[DiscordBot] Bot demarre — mode reponse intelligente + CEO chat")

    gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"
    sequence = None
    resume_url = None
    session_id = None

    while _running:
        try:
            import websockets
            url = resume_url or gateway_url
            async with websockets.connect(url, close_timeout=10, ping_interval=None) as ws:
                # Recevoir Hello (op 10)
                hello_raw = await asyncio.wait_for(ws.recv(), timeout=15)
                hello = json.loads(hello_raw)
                if hello.get("op") != 10:
                    print(f"[DiscordBot] Expected Hello (op10), got op{hello.get('op')}")
                    await asyncio.sleep(5)
                    continue
                heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000

                # Identifier ou Resume
                if session_id and sequence is not None and resume_url:
                    await ws.send(json.dumps({
                        "op": 6,
                        "d": {"token": DISCORD_BOT_TOKEN, "session_id": session_id, "seq": sequence},
                    }))
                    print("[DiscordBot] Resume envoyee")
                else:
                    await ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": DISCORD_BOT_TOKEN,
                            "intents": 512 | 4096 | 32768,  # GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT
                            "properties": {"os": "linux", "browser": "maxia", "device": "maxia"},
                        },
                    }))

                # Heartbeat task avec gestion erreurs
                _hb_ack = True

                async def heartbeat():
                    nonlocal _hb_ack
                    while _running:
                        await asyncio.sleep(heartbeat_interval)
                        if not _hb_ack:
                            print("[DiscordBot] Heartbeat ACK manque — reconnexion")
                            await ws.close(4000)
                            return
                        _hb_ack = False
                        try:
                            await ws.send(json.dumps({"op": 1, "d": sequence}))
                        except Exception:
                            return

                hb_task = asyncio.create_task(heartbeat())

                # Ecouter les events
                while _running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=heartbeat_interval + 10)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"[DiscordBot] Recv error: {e}")
                        break

                    event = json.loads(raw)
                    op = event.get("op")

                    # Heartbeat ACK
                    if op == 11:
                        _hb_ack = True
                        continue

                    # Heartbeat request from Discord
                    if op == 1:
                        await ws.send(json.dumps({"op": 1, "d": sequence}))
                        continue

                    # Reconnect requested
                    if op == 7:
                        print("[DiscordBot] Reconnect demandee par Discord")
                        break

                    # Invalid session
                    if op == 9:
                        print("[DiscordBot] Session invalide — reset")
                        session_id = None
                        resume_url = None
                        sequence = None
                        await asyncio.sleep(3)
                        break

                    # Dispatch (op 0)
                    if op == 0:
                        sequence = event.get("s")
                        event_type = event.get("t")

                        if event_type == "READY":
                            d = event.get("d", {})
                            _bot_user_id = d.get("user", {}).get("id", "")
                            session_id = d.get("session_id", "")
                            resume_url = d.get("resume_gateway_url", "")
                            if resume_url and "?" not in resume_url:
                                resume_url += "?v=10&encoding=json"
                            bot_name = d.get("user", {}).get("username", "?")
                            guilds = len(d.get("guilds", []))
                            print(f"[DiscordBot] Ready — {bot_name} (ID:{_bot_user_id}) — {guilds} serveurs")

                        elif event_type == "RESUMED":
                            print("[DiscordBot] Session resumed OK")

                        elif event_type == "MESSAGE_CREATE":
                            data = event.get("d", {})
                            if data.get("author", {}).get("bot"):
                                continue

                            content = data.get("content", "")
                            channel_id = data.get("channel_id", "")
                            guild_id = data.get("guild_id")
                            is_dm = not guild_id
                            is_mention = _bot_user_id and f"<@{_bot_user_id}>" in content
                            user_name = data.get("author", {}).get("username", "unknown")

                            # === DM ou Mention → CEO MAXIA ===
                            if is_dm or is_mention:
                                clean_msg = content
                                if _bot_user_id:
                                    clean_msg = clean_msg.replace(f"<@{_bot_user_id}>", "").strip()
                                if not clean_msg:
                                    await _send_discord_message(channel_id,
                                        "Je suis le CEO de MAXIA. Pose-moi une question ou donne-moi un ordre.")
                                    continue

                                print(f"[DiscordBot] {'DM' if is_dm else 'Mention'} de {user_name}: {clean_msg[:60]}...")
                                try:
                                    ceo_response = await _ask_ceo(clean_msg, user_name)
                                    await _send_discord_message(channel_id, ceo_response)
                                except Exception as e:
                                    print(f"[DiscordBot] CEO response error: {e}")
                                    await _send_discord_message(channel_id, f"Erreur CEO: {e}")
                                continue

                            # === Serveur: detection mots-cles ===
                            topic = _detect_topic(content)
                            if topic and _check_rate_limit(guild_id):
                                print(f"[DiscordBot] Question detectee ({topic}): {content[:60]}...")
                                await respond_to_message(channel_id, topic)
                                _increment_rate(guild_id)

                hb_task.cancel()

        except Exception as e:
            print(f"[DiscordBot] Connection error: {e}")

        if _running:
            await asyncio.sleep(5)


def stop():
    global _running
    _running = False
