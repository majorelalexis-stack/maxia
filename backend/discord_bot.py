"""MAXIA Discord Bot V11 — Repond aux questions pertinentes (zero spam)

Regles :
- Ne poste JAMAIS de pub spontanee
- Repond uniquement quand quelqu'un pose une question pertinente
- Reponse utile d'abord, mention MAXIA a la fin
- Max 3 reponses/jour par serveur
- Art.1 sur tout le contenu
"""
import asyncio, time, re
import httpx
from config import DISCORD_BOT_TOKEN, GROQ_API_KEY, GROQ_MODEL

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
        "• **RunPod** — $0.69/h (RTX 4090), fiable\n"
        "• **Lambda Labs** — $1.29/h (A100), academique\n"
        "• **MAXIA** — $0.69/h (RTX 4090), prix coutant RunPod, paiement USDC sur Solana, commission 0.05% pour gros volumes\n\n"
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
        "• **MAXIA** — Agregateur, commission 0.05-0.5% selon volume, paiement USDC, API pour bots\n\n"
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
MAX_RESPONSES_PER_SERVER = 3

_running = False


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
    """Envoie une reponse utile sur Discord."""
    if not DISCORD_BOT_TOKEN:
        return

    response = RESPONSE_TEMPLATES.get(topic, "")
    if not response:
        return

    try:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {"content": response}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                print(f"[DiscordBot] Repondu sur topic: {topic}")
            else:
                print(f"[DiscordBot] Erreur {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"[DiscordBot] Erreur envoi: {e}")


async def run_discord_bot():
    """Boucle principale du bot Discord via Gateway WebSocket."""
    global _running
    _running = True

    if not DISCORD_BOT_TOKEN:
        print("[DiscordBot] Token absent — bot desactive")
        return

    print("[DiscordBot] Bot demarre — mode reponse intelligente (zero spam)")

    import websockets

    gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"
    heartbeat_interval = 41250
    sequence = None

    while _running:
        try:
            async with websockets.connect(gateway_url) as ws:
                # Recevoir Hello
                hello = json.loads(await ws.recv())
                heartbeat_interval = hello["d"]["heartbeat_interval"]

                # Identifier
                identify = {
                    "op": 2,
                    "d": {
                        "token": DISCORD_BOT_TOKEN,
                        "intents": 512 | 32768,  # GUILD_MESSAGES + MESSAGE_CONTENT
                        "properties": {
                            "os": "linux",
                            "browser": "maxia",
                            "device": "maxia",
                        },
                    },
                }
                await ws.send(json.dumps(identify))

                # Heartbeat task
                async def heartbeat():
                    while _running:
                        await asyncio.sleep(heartbeat_interval / 1000)
                        await ws.send(json.dumps({"op": 1, "d": sequence}))

                hb_task = asyncio.create_task(heartbeat())

                # Ecouter les messages
                while _running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        event = json.loads(raw)

                        if event.get("op") == 0:
                            sequence = event.get("s")
                            event_type = event.get("t")

                            if event_type == "MESSAGE_CREATE":
                                data = event.get("d", {})
                                # Ignorer nos propres messages
                                if data.get("author", {}).get("bot"):
                                    continue

                                content = data.get("content", "")
                                channel_id = data.get("channel_id", "")
                                guild_id = data.get("guild_id", "")

                                # Detecter si c'est une question pertinente
                                topic = _detect_topic(content)
                                if topic and _check_rate_limit(guild_id):
                                    print(f"[DiscordBot] Question detectee ({topic}): {content[:60]}...")
                                    await respond_to_message(channel_id, topic)
                                    _increment_rate(guild_id)

                        elif event.get("op") == 7:
                            # Reconnect requested
                            break

                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"[DiscordBot] Message error: {e}")
                        break

                hb_task.cancel()

        except Exception as e:
            print(f"[DiscordBot] Connection error: {e}")
            await asyncio.sleep(30)


def stop():
    global _running
    _running = False


import json
