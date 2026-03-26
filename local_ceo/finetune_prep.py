"""MAXIA Fine-Tune Data Preparation — Generateur de donnees d'entrainement.

Collecte les donnees de la memoire du CEO (conversations, commentaires, actions reussies)
et les formate en paires instruction-following (prompt -> completion) compatibles Unsloth/QLoRA.

Objectif : creer un modele "MAXIA Expert" qui :
  - Connait toutes les features MAXIA (swap, GPU, stocks, DeFi, MCP, etc.)
  - Ecrit dans le bon ton (professionnel, calme, confiant)
  - Respecte les regles de personnalite (pas de hype, pas de denigrement, 80% valeur)
  - Sait repondre aux questions techniques sur les 14 chains

Format de sortie : JSONL (1 JSON par ligne) compatible avec :
  - Unsloth (QLoRA fine-tuning)
  - Hugging Face TRL / SFTTrainer
  - Axolotl

Usage:
    from finetune_prep import prepare_finetune_data
    result = await prepare_finetune_data(memory)
    # result = {"samples": 42, "file": "/path/to/finetune_data.jsonl"}
"""
import json
import os
import time
import re


# ══════════════════════════════════════════
# Configuration du fine-tune
# ══════════════════════════════════════════

# Repertoire de sortie
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "finetune")

# System prompt pour le modele fine-tune — encode l'identite MAXIA
_SYSTEM_PROMPT = (
    "You are MAXIA's CEO, an AI managing an AI-to-AI marketplace on 14 blockchains "
    "(Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). "
    "MAXIA offers: 71 token swaps, 25 tokenized stocks, 7 GPU tiers, DeFi yields, "
    "46 MCP tools, 17 AI services, cross-chain bridge, and on-chain escrow. "
    "Tone: professional, calm, confident. Never hype, never denigrate competitors. "
    "80% value, 20% MAXIA mention. Always helpful first."
)

# Mots interdits — filtre qualite (coherent avec config_local.py PERSONALITY)
_FORBIDDEN_WORDS = [
    "revolutionary", "game-changing", "disruptive", "moon", "lambo",
    "100x", "guaranteed", "insane", "mind-blowing",
    "better than", "kills", "destroys", "rip", "dead project",
    "disagree", "wrong", "terrible", "awful", "hate", "stupid",
    "scam", "fraud", "garbage", "trash", "useless",
]


# ══════════════════════════════════════════
# Extraction de donnees depuis la memoire
# ══════════════════════════════════════════

def _extract_conversations(memory: dict) -> list:
    """Extrait les conversations reussies (avec reponse et engagement).
    Format: question utilisateur -> reponse MAXIA."""
    samples = []
    conversations = memory.get("conversations", [])
    for conv in conversations:
        user_msg = conv.get("message", conv.get("question", "")).strip()
        response = conv.get("response", conv.get("reply", conv.get("answer", ""))).strip()
        engagement = conv.get("engagement", conv.get("likes", 0))

        # Filtres qualite
        if not user_msg or not response:
            continue
        if len(user_msg) < 10 or len(response) < 20:
            continue
        # Garder seulement les reponses avec engagement positif ou explicitement reussies
        if engagement <= 0 and not conv.get("success", False):
            continue
        # Verifier absence de mots interdits
        if _contains_forbidden(response):
            continue

        samples.append({
            "instruction": user_msg[:500],
            "output": response[:1000],
            "source": "conversation",
            "engagement": engagement,
            "ts": conv.get("ts", ""),
        })
    return samples


def _extract_comments(memory: dict) -> list:
    """Extrait les commentaires Twitter/Reddit/GitHub qui ont eu de l'engagement.
    Format: contexte du post original -> commentaire MAXIA."""
    samples = []

    # Commentaires Twitter
    tweets = memory.get("tweets_posted", [])
    for tweet in tweets:
        text = tweet.get("text", tweet.get("content", "")).strip()
        engagement = tweet.get("likes", 0) + tweet.get("retweets", 0) + tweet.get("replies", 0)
        context = tweet.get("context", tweet.get("reply_to", "")).strip()

        if not text or len(text) < 20:
            continue
        if engagement <= 0:
            continue
        if _contains_forbidden(text):
            continue

        # Si c'est un commentaire (a un contexte), le formater comme instruction-following
        if context:
            samples.append({
                "instruction": f"Write a helpful reply to this tweet: \"{context[:300]}\"",
                "output": text[:500],
                "source": "twitter_comment",
                "engagement": engagement,
                "ts": tweet.get("ts", ""),
            })
        else:
            # Tweet original — l'instruction est de generer du contenu
            samples.append({
                "instruction": "Write a tweet about MAXIA's AI marketplace that provides value without being promotional.",
                "output": text[:500],
                "source": "twitter_original",
                "engagement": engagement,
                "ts": tweet.get("ts", ""),
            })

    # Commentaires Reddit
    reddit_posts = memory.get("reddit_posts", [])
    for post in reddit_posts:
        text = post.get("text", post.get("content", "")).strip()
        engagement = post.get("upvotes", 0) + post.get("comments", 0)
        subreddit = post.get("subreddit", "")
        context = post.get("context", post.get("reply_to", "")).strip()

        if not text or len(text) < 30:
            continue
        if engagement <= 0:
            continue
        if _contains_forbidden(text):
            continue

        instruction = f"Write a helpful comment on r/{subreddit}"
        if context:
            instruction += f" about: \"{context[:200]}\""
        samples.append({
            "instruction": instruction,
            "output": text[:800],
            "source": "reddit",
            "engagement": engagement,
            "ts": post.get("ts", ""),
        })

    # Commentaires GitHub
    github_comments = memory.get("github_comments", [])
    for comment in github_comments:
        text = comment.get("text", comment.get("body", "")).strip()
        repo = comment.get("repo", "")
        context = comment.get("context", comment.get("issue_title", "")).strip()

        if not text or len(text) < 30:
            continue
        if _contains_forbidden(text):
            continue

        instruction = f"Write a helpful comment on GitHub"
        if repo:
            instruction += f" repo {repo}"
        if context:
            instruction += f" about: \"{context[:200]}\""
        samples.append({
            "instruction": instruction,
            "output": text[:800],
            "source": "github",
            "engagement": comment.get("reactions", 0),
            "ts": comment.get("ts", ""),
        })

    return samples


def _extract_successful_actions(memory: dict) -> list:
    """Extrait les actions reussies et les formate comme exemples d'instruction-following.
    Format: description de la situation -> action prise + resultat."""
    samples = []
    actions = memory.get("actions_done", [])
    for action in actions:
        if not action.get("success", False):
            continue

        act_type = action.get("action", action.get("type", "")).strip()
        params = action.get("params", action.get("details", {}))
        result = action.get("result", action.get("outcome", "")).strip() if isinstance(action.get("result", ""), str) else str(action.get("result", ""))

        if not act_type:
            continue

        # Construire l'instruction basee sur le type d'action
        if isinstance(params, dict):
            params_str = json.dumps(params, default=str)[:200]
        else:
            params_str = str(params)[:200]

        instruction = f"As MAXIA CEO, decide how to handle: {act_type}"
        if params_str and params_str != "{}":
            instruction += f" with context: {params_str}"

        output = f"Action: {act_type}"
        if result and len(result) > 5:
            output += f"\nResult: {result[:300]}"

        if len(instruction) < 20 or len(output) < 20:
            continue

        samples.append({
            "instruction": instruction[:500],
            "output": output[:500],
            "source": "action",
            "engagement": 1,  # Succes = engagement positif
            "ts": action.get("ts", ""),
        })
    return samples


def _extract_research_knowledge(memory: dict) -> list:
    """Extrait les connaissances R&D pour entrainer la comprehension du domaine.
    Format: question sur un sujet -> explication basee sur les findings."""
    samples = []
    findings = memory.get("research_findings", [])
    for finding in findings:
        target = finding.get("target", "").strip()
        category = finding.get("category", "").strip()
        content = finding.get("finding", "").strip()

        if not content or len(content) < 30:
            continue

        # Generer une instruction plausible basee sur la categorie
        if category == "competition":
            instruction = f"What do you know about {target} as a competitor in the AI/crypto space?"
        elif category == "opportunity":
            instruction = f"What opportunities exist in {target} for an AI marketplace?"
        elif category == "improvement":
            instruction = f"How could MAXIA improve based on insights from {target}?"
        else:
            instruction = f"Explain what you found about {target} in your R&D research."

        samples.append({
            "instruction": instruction[:500],
            "output": content[:800],
            "source": "research",
            "engagement": 1,
            "ts": finding.get("ts", ""),
        })
    return samples


def _extract_strategy_decisions(memory: dict) -> list:
    """Extrait les decisions strategiques validees.
    Format: situation -> decision prise et justification."""
    samples = []
    decisions = memory.get("decisions", [])
    for dec in decisions:
        situation = dec.get("situation", dec.get("context", "")).strip()
        decision = dec.get("decision", dec.get("action", "")).strip()
        reason = dec.get("reason", dec.get("justification", "")).strip()

        if not decision or len(decision) < 10:
            continue

        instruction = f"As MAXIA CEO, what decision would you make in this situation: {situation[:300]}" if situation else "Make a strategic decision for MAXIA."
        output = decision[:400]
        if reason:
            output += f"\nReasoning: {reason[:300]}"

        if len(output) < 20:
            continue

        samples.append({
            "instruction": instruction[:500],
            "output": output[:700],
            "source": "decision",
            "engagement": 1,
            "ts": dec.get("ts", ""),
        })
    return samples


# ══════════════════════════════════════════
# Filtres qualite
# ══════════════════════════════════════════

def _contains_forbidden(text: str) -> bool:
    """Verifie si le texte contient des mots interdits."""
    text_lower = text.lower()
    for word in _FORBIDDEN_WORDS:
        if word in text_lower:
            return True
    return False


def _deduplicate(samples: list) -> list:
    """Supprime les doublons basees sur l'instruction (meme question = meme sample)."""
    seen = set()
    unique = []
    for s in samples:
        # Normaliser l'instruction pour la deduplication
        key = re.sub(r'\s+', ' ', s["instruction"].lower().strip())[:200]
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _quality_score(sample: dict) -> float:
    """Score de qualite d'un sample (0-1). Utilise pour le tri."""
    score = 0.0

    # Engagement (plus c'est engage, mieux c'est)
    eng = sample.get("engagement", 0)
    if eng > 10:
        score += 0.4
    elif eng > 5:
        score += 0.3
    elif eng > 0:
        score += 0.2

    # Longueur de la reponse (ni trop court, ni trop long)
    output_len = len(sample.get("output", ""))
    if 50 <= output_len <= 500:
        score += 0.3
    elif 20 <= output_len <= 800:
        score += 0.2
    elif output_len > 800:
        score += 0.1

    # Source (conversations > comments > actions > research)
    source_scores = {
        "conversation": 0.3,
        "twitter_comment": 0.25,
        "reddit": 0.2,
        "github": 0.2,
        "twitter_original": 0.15,
        "decision": 0.15,
        "action": 0.1,
        "research": 0.1,
    }
    score += source_scores.get(sample.get("source", ""), 0.05)

    return min(score, 1.0)


# ══════════════════════════════════════════
# Formatage JSONL (Unsloth / QLoRA compatible)
# ══════════════════════════════════════════

def _format_for_unsloth(sample: dict) -> dict:
    """Formate un sample pour Unsloth/QLoRA (format ChatML).
    Compatible avec SFTTrainer de Hugging Face TRL."""
    return {
        "conversations": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": sample["instruction"]},
            {"role": "assistant", "content": sample["output"]},
        ],
    }


def _format_alpaca(sample: dict) -> dict:
    """Format Alpaca alternatif (instruction, input, output)."""
    return {
        "instruction": sample["instruction"],
        "input": "",
        "output": sample["output"],
        "system": _SYSTEM_PROMPT,
    }


# ══════════════════════════════════════════
# Fonction principale
# ══════════════════════════════════════════

async def prepare_finetune_data(memory: dict) -> dict:
    """Prepare les donnees de fine-tuning depuis la memoire du CEO.

    Collecte les conversations, commentaires, actions reussies et connaissances R&D.
    Filtre par qualite (engagement > 0, pas de mots interdits).
    Exporte en JSONL compatible Unsloth/QLoRA.

    Args:
        memory: dict de la memoire du CEO local (ceo_memory.json)

    Returns:
        {
            "samples": int (nombre de samples generes),
            "file": str (chemin du fichier JSONL),
            "stats": {
                "conversations": int,
                "comments": int,
                "actions": int,
                "research": int,
                "decisions": int,
                "filtered_out": int,
                "duplicates_removed": int,
            }
        }
    """
    print("[FINETUNE] Preparation des donnees d'entrainement...")

    # Creer le repertoire de sortie
    os.makedirs(_OUTPUT_DIR, exist_ok=True)

    # ── Collecter toutes les sources ──
    conversations = _extract_conversations(memory)
    comments = _extract_comments(memory)
    actions = _extract_successful_actions(memory)
    research = _extract_research_knowledge(memory)
    decisions = _extract_strategy_decisions(memory)

    total_raw = len(conversations) + len(comments) + len(actions) + len(research) + len(decisions)
    print(f"[FINETUNE] Donnees brutes: {len(conversations)} conversations, "
          f"{len(comments)} comments, {len(actions)} actions, "
          f"{len(research)} research, {len(decisions)} decisions = {total_raw} total")

    # ── Combiner et dedupliquer ──
    all_samples = conversations + comments + actions + research + decisions
    before_dedup = len(all_samples)
    all_samples = _deduplicate(all_samples)
    duplicates_removed = before_dedup - len(all_samples)

    # ── Scorer et trier par qualite ──
    for s in all_samples:
        s["_quality"] = _quality_score(s)
    all_samples.sort(key=lambda x: x["_quality"], reverse=True)

    # ── Filtrer les samples de mauvaise qualite (score < 0.15) ──
    filtered_samples = [s for s in all_samples if s["_quality"] >= 0.15]
    filtered_out = len(all_samples) - len(filtered_samples)

    print(f"[FINETUNE] Apres filtrage: {len(filtered_samples)} samples "
          f"({duplicates_removed} doublons supprimes, {filtered_out} filtres par qualite)")

    # ── Exporter en JSONL (format ChatML pour Unsloth) ──
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(_OUTPUT_DIR, f"maxia_finetune_{timestamp}.jsonl")
    output_file_alpaca = os.path.join(_OUTPUT_DIR, f"maxia_finetune_{timestamp}_alpaca.jsonl")

    # Format ChatML (Unsloth principal)
    with open(output_file, "w", encoding="utf-8") as f:
        for sample in filtered_samples:
            formatted = _format_for_unsloth(sample)
            f.write(json.dumps(formatted, ensure_ascii=False) + "\n")

    # Format Alpaca (alternatif)
    with open(output_file_alpaca, "w", encoding="utf-8") as f:
        for sample in filtered_samples:
            formatted = _format_alpaca(sample)
            f.write(json.dumps(formatted, ensure_ascii=False) + "\n")

    # ── Sauvegarder les metadonnees ──
    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_samples": len(filtered_samples),
        "sources": {
            "conversations": len(conversations),
            "comments": len(comments),
            "actions": len(actions),
            "research": len(research),
            "decisions": len(decisions),
        },
        "quality": {
            "min_score": min((s["_quality"] for s in filtered_samples), default=0),
            "max_score": max((s["_quality"] for s in filtered_samples), default=0),
            "avg_score": sum(s["_quality"] for s in filtered_samples) / max(1, len(filtered_samples)),
        },
        "files": {
            "chatml": output_file,
            "alpaca": output_file_alpaca,
        },
        "model_target": "MAXIA Expert (Qwen 14B + QLoRA via Unsloth)",
        "system_prompt": _SYSTEM_PROMPT,
        "duplicates_removed": duplicates_removed,
        "filtered_out": filtered_out,
    }
    meta_file = os.path.join(_OUTPUT_DIR, f"maxia_finetune_{timestamp}_meta.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"[FINETUNE] Exporte: {output_file} ({len(filtered_samples)} samples)")
    print(f"[FINETUNE] Alpaca:  {output_file_alpaca}")
    print(f"[FINETUNE] Meta:    {meta_file}")

    # Nettoyer le champ _quality temporaire
    for s in filtered_samples:
        s.pop("_quality", None)

    return {
        "samples": len(filtered_samples),
        "file": output_file,
        "file_alpaca": output_file_alpaca,
        "meta_file": meta_file,
        "stats": {
            "conversations": len(conversations),
            "comments": len(comments),
            "actions": len(actions),
            "research": len(research),
            "decisions": len(decisions),
            "filtered_out": filtered_out,
            "duplicates_removed": duplicates_removed,
        },
    }
