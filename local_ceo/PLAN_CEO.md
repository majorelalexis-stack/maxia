# PLAN CEO V4.1 — Refonte complete du CEO Local MAXIA

**Date**: 3 avril 2026 (V4.1: mise a jour meme date)
**Auteur**: Claude Opus + Alexis
**Declencheur**: "lance le plan CEO" ou "CEO-X"
**Hardware**: RX 7900XT (20GB VRAM) + 5800X (8C/16T) + 32GB RAM DDR4 (6GB dedies IA) + NVMe SSD

---

## CHANGEMENTS V4 → V4.1

| Element | V4 | V4.1 |
|---------|-----|------|
| VPS CEO code | Existait (ceo_api.py, etc.) | **SUPPRIME** — 17 fichiers, ~10K lignes |
| Alertes Telegram/Discord VPS | Actives | **COUPEES** (kill switch alerts.py) |
| Bots VPS (Telegram/Discord/Twitter) | Fichiers presents | **SUPPRIMES** |
| Knowledge Base | 118 lignes (surface) | **670 lignes** (technique profonde) |
| Memoire | SQLite + ChromaDB | **3 couches** (Session + Compressed + Vector) |
| Self-learning | Non prevu | **Integre** (724-office pattern + Unsloth optionnel) |
| Nombre de sessions | 6 (CEO-1 a CEO-6) | **7** (CEO-0 ajoute) |
| CEO-0 | N'existait pas | **Knowledge Base Deep** (FAIT) |

### VPS apres nettoyage
Le VPS ne contient plus AUCUN code CEO. Le marketplace tourne seul :
- Swaps, escrow, GPU, prix, DeFi, agent_worker, brain → OK
- Pas de Telegram/Discord/Twitter bots
- Pas de CEO API endpoints
- Le CEO Local appelle uniquement les endpoints PUBLICS du VPS

---

## DIAGNOSTIC — Etat actuel

### Ce qui existe
- `ceo_local_v2.py` — 2164 lignes monolithiques, 14+ missions
- `browser_agent.py` — 144KB, automation Playwright (Twitter, Reddit, GitHub)
- `email_manager.py` — IMAP/SMTP OVH complet (read_inbox, send_email, auto-reply, outbound prospect) — **SOUS-UTILISE**
- `config_local.py` — config modeles + limites
- `vector_memory_local.py` — ChromaDB memoire semantique
- 5 fichiers JSON memoire non synchronises
- Knowledge base: `maxia_knowledge.md` (118 lignes)

### Problemes identifies
1. **Monolithe** — 2164 lignes, impossible a maintenir
2. **Zero feedback loop** — le CEO ne sait pas si ses actions marchent
3. **Twitter flagge** pour spam — compte a risque
4. **Email sous-utilise** — `process_inbox()` et `send_outbound_prospect()` jamais appeles
5. **Code mort** — Kaspa mining, Discord scan vide, ceo_local.py (310KB ancien)
6. **Memoire fragmentee** — 5 JSON non synchronises, pas cross-restart safe
7. **Missions redondantes** — 14 missions dont beaucoup se chevauchent
8. **Trop de mails** — Alexis noye de rapports "tout va bien"
9. **Resultats** — 0 revenu, 0 client apres des semaines

---

## PHASE 0 — Choix du modele (FONDATION)

### Benchmark modeles Ollama pour 20GB VRAM

| Modele | Type | VRAM | Params actifs | Context | Vision | Verdict |
|--------|------|------|---------------|---------|--------|---------|
| **qwen3.5:27b** | Dense | **17GB** | **27.8B** | 256K | **OUI** | **RECOMMANDE** |
| qwen3:14b | Dense | 9.3GB | 14B | 40K | Non | Backup leger |
| qwen3:30b | **MoE** | 19GB | **3B seulement** | 256K | Non | **PIEGE** (3B actif!) |
| qwen3:32b | Dense | 20GB | 32B | 40K | Non | Trop juste |
| qwen3.5:9b | Dense | 6.6GB | 9B | 256K | OUI | Ultra-leger |
| devstral:24b | Dense | 14GB | 24B | 128K | Non | Code only |
| gemma4:26b | MoE | 18GB | 3.8B | 256K | OUI | Trop peu actif |

### ATTENTION : qwen3:30b est un piege
MoE "A3B" = seulement 3B de parametres actifs par token. 19GB en VRAM pour la qualite d'un 3B dense. **PIRE** que qwen3:14b.

### Choix : qwen3.5:27b (17GB)

**Pourquoi :**
1. Dense 27.8B = vraie intelligence (pas du MoE dilue)
2. Multimodal natif (texte + image) = remplace CEO + vision en 1 seul modele
3. 256K context = peut ingerer toute la knowledge base
4. 17GB + ~2GB KV cache = ~19GB, reste dans les 20GB VRAM
5. Pas d'overflow en RAM → RAM libre pour Playwright + Python

### Budget RAM (6GB DDR4)

| Process | RAM |
|---------|-----|
| OS + background | ~2GB |
| Python CEO | ~200MB |
| Playwright/Chromium | ~1GB |
| Ollama overhead | ~200MB |
| Libre | ~2.5GB |
| **Total** | **~6GB** |

### Config Ollama

```bash
# Environnement
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_NUM_PARALLEL=1
OLLAMA_FLASH_ATTENTION=1

# Modele
ollama pull qwen3.5:27b
```

```python
# config_local.py
OLLAMA_MODEL = "qwen3.5:27b"
# Plus besoin de VISION_MODEL — qwen3.5 est multimodal natif
```

### Alternative si RAM trop juste
`qwen3.5:9b` (6.6GB) — meme architecture multimodale, 256K context, qualite moindre mais CEO fonctionne. Laisse 13.4GB pour KV cache.

---

## PHASE 1 — Sub-agents virtuels (meme modele, prompts differents)

Pas de vrai sub-agent (trop de RAM). Le **meme qwen3.5:27b** avec des system prompts specialises :

### 5 agents virtuels

| Agent | Mode think | Temp | Usage | Timeout |
|-------|-----------|------|-------|---------|
| **Strategist** | ON | 0.3 | Analyse strategique, pivots, decisions, revue semaine | 300s |
| **Writer** | OFF | 0.7 | Tweets, emails prospect, rapports, messages contact | 60s |
| **Analyst** | ON | 0.5 | Scoring opportunites, evaluation agents, veille | 120s |
| **Monitor** | OFF | 0.1 | Health check, moderation spam, classification emails | 30s |
| **Chat** | ON | 0.5 | Conversation avec Alexis (Telegram + console) | 120s |

### Implementation

```python
# agents.py
from dataclasses import dataclass

@dataclass(frozen=True)
class AgentConfig:
    name: str
    system_prompt: str
    think: bool
    max_tokens: int
    temperature: float
    timeout: int

STRATEGIST = AgentConfig(
    name="strategist",
    system_prompt=(
        "You are the MAXIA CEO Strategic Advisor. You analyze business metrics, "
        "competitive intelligence, and market signals to make data-driven decisions. "
        "Think deeply before answering. Focus on: what's working, what's not, "
        "what to try next. Always back recommendations with data."
    ),
    think=True, max_tokens=1000, temperature=0.3, timeout=300,
)

WRITER = AgentConfig(
    name="writer",
    system_prompt=(
        "You are the MAXIA CEO Content Writer. You craft tweets, emails, and reports. "
        "Rules: Professional tone. No hype words (revolutionary, game-changing, moon, lambo). "
        "80% value, 20% MAXIA mention. Include maxiaworld.app link when relevant. "
        "Max 280 chars for tweets. Max 150 words for emails."
    ),
    think=False, max_tokens=300, temperature=0.7, timeout=60,
)

ANALYST = AgentConfig(
    name="analyst",
    system_prompt=(
        "You are the MAXIA CEO Analyst. You score opportunities, evaluate AI agents, "
        "and assess competitive threats. Use structured scoring (1-10). "
        "Score 8-10: autonomous agents that could sell/buy on MAXIA. "
        "Score 5-7: technical tools with integration potential. "
        "Score 1-4: social bots, influencers, chatbots — not relevant."
    ),
    think=True, max_tokens=500, temperature=0.5, timeout=120,
)

MONITOR = AgentConfig(
    name="monitor",
    system_prompt=(
        "You are the MAXIA Health Monitor. Classify inputs as OK/WARNING/CRITICAL. "
        "Be terse. Only flag real problems, not noise."
    ),
    think=False, max_tokens=200, temperature=0.1, timeout=30,
)

CHAT = AgentConfig(
    name="chat",
    system_prompt=(
        "You are the MAXIA CEO Assistant. Alexis (the founder) is chatting with you. "
        "You know EVERYTHING about MAXIA (see knowledge base injected below). "
        "Answer in French. Be concise but precise. If asked about status, "
        "query the latest data from memory/SQLite before answering. "
        "If asked to DO something (send email, post tweet, change strategy), "
        "confirm the action and queue it — don't execute blindly. "
        "For ORANGE/RED actions, ask for explicit approval."
    ),
    think=True, max_tokens=500, temperature=0.5, timeout=120,
)
```

### Appel unifie

```python
# llm.py
async def ask(agent: AgentConfig, prompt: str) -> str:
    """Appel Ollama avec config agent."""
    return await _ollama_generate(
        model=OLLAMA_MODEL,
        prompt=prompt,
        system=agent.system_prompt,
        think=agent.think,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
        timeout=agent.timeout,
    )
```

---

## PHASE 2 — Missions redesignees (7 au lieu de 14)

### Vue d'ensemble

| # | Mission | Frequence | Agent | LLM? | Email? |
|---|---------|-----------|-------|------|--------|
| 1 | Health Monitor | 5 min | Monitor | Non (HTTP) | Alerte si down |
| 2 | Daily Tweet | 14h-17h, 1x/jour | Writer | Oui | Non |
| 3 | Email Outreach | 10h, 1x/jour | Writer | Oui | **OUI — cle** |
| 4 | Opportunity Scan | 18h30, 1x/jour | Analyst | Oui | Resume a Alexis |
| 5 | Scout AI | 17h, 1x/jour | Analyst | Oui | GO/SKIP a Alexis |
| 6 | Competitive Watch | 1x/semaine | Strategist | Oui | Rapport |
| 7 | Strategy Review | Dimanche 20h | Strategist | Oui (think=on) | Bilan semaine |

### Mission 1 — Health Monitor (toutes les 5 min)

**Pas de LLM.** Simple HTTP check.

```
- Ping 5 endpoints (site, prices, forum, stats, mcp)
- Log latences dans SQLite
- Alerte mail UNIQUEMENT si down (pas de "tout va bien")
- Inclut: moderation forum basique (regex, pas de LLM)
- Inclut: check inbox ceo@maxiaworld.app → process_inbox() pour auto-reply
```

**Ce qui change:** Plus de health report quotidien "tout va bien". Mail uniquement si probleme. Auto-reply email integre.

### Mission 2 — Daily Tweet (14h-17h)

```
- 1 tweet feature/jour via browser_agent
- Agent: Writer (think=off, creatif)
- Rotation features avec memoire SQLite (pas de repeat < 7 jours)
- 1 jour off aleatoire par semaine
- Anti-spam: cosine similarity < 0.8 vs 5 derniers tweets
- Si LLM genere un tweet trop similaire → regenere 1 fois, sinon skip
```

### Mission 3 — Email Outreach (10h, 1x/jour) — **NOUVELLE, PRIORITAIRE**

Le CEO utilise `ceo@maxiaworld.app` pour du outreach B2B cible.

```
Pipeline:
1. Prendre les agents Scout approuves (status=approved) qui ont un email
2. Generer un cold email personnalise via Writer agent
3. Envoyer via email_manager.send_outbound_prospect()
4. Max 3 emails outbound/jour (prudent au debut)
5. Logger dans SQLite (to, subject, date, status)
6. Si reply recu → process_inbox() genere auto-reply + flag Alexis

Sources d'emails:
- GitHub profiles (public email)
- README/CONTRIBUTING des projets scouts
- Sites web des projets (contact page)
- Agentverse/Smithery profiles

Template email (genere par Writer, pas hardcode):
- Subject: personnalise au projet du prospect
- Body: ce que MAXIA apporte A EUX specifiquement
- CTA: "reply to discuss" ou "visit maxiaworld.app"
- Signature: MAXIA Team + lien
- Max 150 mots, ton developpeur, pas commercial
```

**Pourquoi c'est la mission la plus importante:**
- Email = 0 risque de ban (contrairement a Twitter)
- B2B cible = meilleure conversion que tweets publics
- Personnalise = LLM adapte le message au projet
- Tracable = on sait qui a repondu
- L'infra existe deja (`email_manager.py`) et n'est pas utilisee

### Mission 4 — Opportunity Scan (18h30, 1x/jour)

```
- Scan Twitter + GitHub + Reddit en 1 pass
- Agent: Analyst (think=on, scoring)
- Top 5 scorees → 1 mail resume a Alexis
- Avec: lien, contexte, 3 variantes de commentaire
- Fusionne les anciennes missions 2, 2b, 3
- Si email du prospect trouve → l'ajouter en candidat Mission 3
```

### Mission 5 — Scout AI (17h, 1x/jour)

```
- Scan 8 registries (Virtuals, Agentverse, Smithery, ElizaOS, GitHub, etc.)
- Agent: Analyst (scoring)
- Pour chaque agent score >= 7:
  - Extraire email si disponible (GitHub profile, README)
  - Generer message de contact (Writer agent)
- Mail GO/SKIP a Alexis avec les candidats
- Apres GO: status → approved, disponible pour Mission 3 (email)
- Si pas d'email: status → manual (Alexis contacte lui-meme)
```

### Mission 6 — Competitive Watch (1x/semaine, dimanche 19h)

```
- Scan 4 concurrents (Virtuals, Olas, CrewAI, Fetch.ai)
- Agent: Strategist (think=on)
- Rapport: nombre d'agents, activite, features, tendances
- Comparaison avec metriques MAXIA
- Recommandations strategiques
- 1 mail a Alexis
```

### Mission 7 — Strategy Review (dimanche 20h) — **NOUVELLE, CRITIQUE**

```
- Collecte metriques semaine:
  - Signups agents (via /api/public/leaderboard)
  - Emails envoyes / reponses recues
  - Tweet engagement (scrape 24h apres post)
  - Visites web (si endpoint dispo)
- Agent: Strategist (think=on, max_tokens=1000)
- Analyse: qu'est-ce qui a marche? qu'est-ce qui n'a pas marche?
- Objectif semaine suivante (mesurable: "X signups", "Y replies email")
- Score semaine: reel / objectif x 100
- Si score < 30% pendant 2 semaines → PIVOT:
  - Strategist genere 3 hypotheses alternatives
  - Choisit la meilleure
  - Ajuste la strategie pour la semaine suivante
- Stocke learnings dans ChromaDB + SQLite
- Mail bilan a Alexis
```

### Missions SUPPRIMEES

| Ancienne mission | Raison |
|-----------------|--------|
| Forum moderation (LLM) | → Regex dans Health Monitor, pas besoin de LLM |
| Code Audit | Claude Code fait 100x mieux |
| Changelog forum | Pas de communaute active |
| Health report mail quotidien | Fusionne: alerte uniquement si probleme |
| Daily report GitHub | Fusionne dans Opportunity Scan |
| Check emails Alexis (standalone) | Fusionne dans Health Monitor |
| Discord scan | Etait vide (`pass`) |
| Morning Brief | Trop de mails, supprime |

---

## PHASE 3 — Memoire 3 couches (Session + Compressed + Vector)

Architecture inspiree de **724-office** (github.com/wangziqi06/724-office) — agent 24/7 avec memoire persistante qui apprend.

### Vue d'ensemble

```
┌──────────────────────────────────────────────────┐
│ LAYER 1: SESSION (RAM)                            │
│ Contexte mission en cours, disparait au restart   │
│ Rapide, ~100KB max                                │
├──────────────────────────────────────────────────┤
│ LAYER 2: COMPRESSED (SQLite SSD)                  │
│ Faits & learnings comprimes par le LLM            │
│ "Email Olas → 2 replies en 1 sem"                 │
│ "Tweets GPU = 3x plus engagement que DeFi"        │
│ Dedup Jaccard 0.92, retention infinie              │
│ Injecte dans prompts quand pertinent               │
├──────────────────────────────────────────────────┤
│ LAYER 3: VECTOR (ChromaDB SSD)                    │
│ Tout l'historique en embeddings                   │
│ Recherche semantique "similar to X"               │
│ Deja en place (vector_memory_local.py)            │
│ 4 collections: actions, decisions, contacts,       │
│ learnings                                          │
└──────────────────────────────────────────────────┘
```

### Layer 1 — Session (RAM)

```python
# Dans ceo_main.py — contexte courant en memoire
session = {
    "current_mission": "email_outreach",
    "started_at": time.time(),
    "actions_this_run": [],
    "errors": [],
}
# Reset a chaque restart. Pas de persistence.
```

### Layer 2 — Compressed (SQLite SSD)

Remplace les 5+ fichiers JSON par 1 SQLite persistent.

```sql
-- ceo_state.db

CREATE TABLE actions (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    type TEXT NOT NULL,       -- tweet, email_out, email_reply, scan, scout, health, strategy
    target TEXT,              -- destinataire ou sujet
    details TEXT,             -- JSON blob
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE tweets (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    feature TEXT NOT NULL,
    text TEXT NOT NULL,
    engagement TEXT,          -- JSON: {impressions, likes, retweets, clicks}
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE emails (
    id INTEGER PRIMARY KEY,
    direction TEXT NOT NULL,  -- inbound, outbound, reply
    address TEXT NOT NULL,
    subject TEXT,
    body_preview TEXT,        -- premiers 200 chars
    status TEXT DEFAULT 'sent',  -- sent, replied, bounced, no_reply
    related_scout_id TEXT,    -- lien vers scout_agents si prospection
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE opportunities (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,   -- twitter, github, reddit
    ext_id TEXT UNIQUE,
    text TEXT,
    score INTEGER,
    suggested_reply TEXT,
    email TEXT,               -- email du prospect si trouve
    status TEXT DEFAULT 'pending',  -- pending, sent, approved, contacted, rejected
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE scout_agents (
    id INTEGER PRIMARY KEY,
    ext_id TEXT UNIQUE,
    name TEXT,
    registry TEXT,
    chain TEXT,
    score INTEGER,
    email TEXT,               -- email du projet/owner
    contact_message TEXT,
    status TEXT DEFAULT 'discovered',  -- discovered, pending, approved, contacted, rejected, manual
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE metrics (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    type TEXT NOT NULL,       -- signups, web_visits, twitter_engagement, email_replies
    data TEXT,                -- JSON blob
    created_at REAL DEFAULT (unixepoch())
);

-- NOUVEAU V4.1 : learnings avec score de confiance + decay
CREATE TABLE learnings (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    insight TEXT NOT NULL,       -- compresse par le LLM (Layer 2)
    source TEXT,                 -- strategy_review, twitter_analysis, competitive, email_feedback
    confidence REAL DEFAULT 0.5, -- 0.0 = hypothese, 1.0 = prouve
    times_confirmed INTEGER DEFAULT 0,  -- +1 chaque fois que le learning est re-confirme
    times_contradicted INTEGER DEFAULT 0, -- +1 si contredit par les faits
    last_used REAL,              -- dernier usage dans un prompt
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE strategy (
    id INTEGER PRIMARY KEY,
    week TEXT NOT NULL UNIQUE, -- 2026-W14
    objectives TEXT,           -- JSON: [{metric, target, actual}]
    score INTEGER,
    analysis TEXT,
    next_actions TEXT,         -- JSON
    created_at REAL DEFAULT (unixepoch())
);

-- Index pour les requetes frequentes
CREATE INDEX idx_actions_date ON actions(date);
CREATE INDEX idx_actions_type ON actions(type);
CREATE INDEX idx_emails_status ON emails(status);
CREATE INDEX idx_scout_status ON scout_agents(status);
CREATE INDEX idx_metrics_date ON metrics(date);
CREATE INDEX idx_strategy_week ON strategy(week);
CREATE INDEX idx_learnings_confidence ON learnings(confidence DESC);
CREATE INDEX idx_learnings_topic ON learnings(topic);
```

### Fichiers JSON remplaces

| Ancien fichier | → Table SQLite |
|----------------|----------------|
| ceo_memory.json (66KB) | actions, tweets, opportunities |
| actions_today.json | actions (filtre date=today) |
| scout_discoveries.json (32KB) | scout_agents |
| scout_pending_contacts.json (7KB) | scout_agents (status=pending) |
| platform_scores.json | metrics |
| learnings.json | learnings |
| strategy.md | strategy |

### Layer 3 — Vector (ChromaDB SSD)

Deja en place : `local_ceo/vector_memory_local.py` + `local_ceo/ceo_vector_db/`

4 collections existantes :
- **actions** — tweets, replies, comments (dedup semantique)
- **decisions** — choix strategiques
- **contacts** — infos sur prospects/users
- **learnings** — regles et apprentissages (miroir semantique de la table SQLite)

### Self-Learning : comment le CEO apprend

```
BOUCLE HEBDOMADAIRE (Strategy Review, dimanche 20h):

1. COLLECTER — metriques semaine (signups, replies, engagement)
2. ANALYSER — Strategist agent (think=on) analyse : qu'est-ce qui a marche?
3. COMPRIMER — Le LLM resume chaque insight en 1-2 phrases
   Exemple: "Les emails ciblant des projets Olas avec mention GPU
   generent 3x plus de replies que les emails generiques"
4. DEDUP — Jaccard similarity > 0.92 avec learnings existants?
   - Oui → incrementer times_confirmed, mettre a jour confidence
   - Non → creer nouveau learning (confidence=0.5)
5. STOCKER — SQLite (Layer 2) + ChromaDB (Layer 3)
6. CONTREDIRE — Si un learning est contredit par les faits:
   - times_contradicted += 1
   - Si contradicted > confirmed → confidence = 0.1 (quasi-retire)
7. INJECTER — Avant chaque mission, les top 5 learnings pertinents
   sont injectes dans le system prompt de l'agent

RESULTAT : Le CEO s'ameliore CHAQUE SEMAINE sans fine-tuning.
```

### Implementation dans memory.py

```python
# memory.py — fonctions self-learning

async def compress_and_store_learning(llm_ask, raw_data: str, source: str) -> dict:
    """Comprimer un fait brut en learning via LLM, puis stocker."""
    # 1. Demander au LLM de comprimer
    prompt = f"Compress this observation into ONE actionable insight (max 30 words):\n{raw_data}"
    insight = await llm_ask(MONITOR, prompt)  # Agent monitor (rapide, pas de think)

    # 2. Check dedup Jaccard avec learnings existants
    existing = db.execute("SELECT id, insight FROM learnings WHERE topic = ?", [source])
    for row in existing:
        if jaccard_similarity(insight, row["insight"]) > 0.92:
            # Learning deja connu → confirmer
            db.execute("UPDATE learnings SET times_confirmed = times_confirmed + 1, confidence = MIN(1.0, confidence + 0.1) WHERE id = ?", [row["id"]])
            return {"action": "confirmed", "id": row["id"]}

    # 3. Nouveau learning
    db.execute("INSERT INTO learnings (topic, insight, source, confidence) VALUES (?, ?, ?, 0.5)", [source, insight, source])
    # 4. Aussi dans ChromaDB pour recherche semantique
    vector_mem.store("learnings", insight, {"source": source, "date": today()})
    return {"action": "created", "insight": insight}


def get_relevant_learnings(topic: str, limit: int = 5) -> list[str]:
    """Recuperer les learnings les plus pertinents pour un topic."""
    # Combiner: SQLite (par confidence) + ChromaDB (par similarite)
    sql_results = db.execute(
        "SELECT insight FROM learnings WHERE confidence > 0.3 ORDER BY confidence DESC, times_confirmed DESC LIMIT ?",
        [limit]
    )
    vector_results = vector_mem.search("learnings", topic, n=limit)
    # Fusionner et deduper
    return deduplicate(sql_results + vector_results)
```

### Dedup Jaccard (724-office pattern)

```python
def jaccard_similarity(a: str, b: str) -> float:
    """Similarite Jaccard entre 2 textes (mots)."""
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)
```

### Nettoyage automatique

```sql
-- Supprimer les donnees > 90 jours (SAUF learnings et strategy = memoire permanente)
DELETE FROM actions WHERE created_at < unixepoch() - 7776000;
DELETE FROM emails WHERE created_at < unixepoch() - 7776000;
DELETE FROM metrics WHERE created_at < unixepoch() - 7776000;
-- Learnings a faible confiance > 180 jours → purge
DELETE FROM learnings WHERE confidence < 0.2 AND created_at < unixepoch() - 15552000;
```

### OPTIONNEL (futur) : Unsloth LoRA Fine-Tune Mensuel

Repo: github.com/unslothai/unsloth (57.8k stars, Apache 2.0)
- Fine-tuning 2x plus rapide, 70% moins VRAM
- Supporte Qwen, ROCm AMD bientot (pertinent pour 7900XT)

```
BOUCLE MENSUELLE (optionnel, CEO-7 futur):
1. Exporter les top learnings (confidence > 0.8) en JSONL
2. Formater en paires instruction/response
3. LoRA fine-tune qwen3.5:27b via Unsloth (1-2h sur 7900XT)
4. Valider sur 10 questions test
5. Si score > seuil → remplacer le modele Ollama
6. Le modele LUI-MEME s'ameliore (pas juste le contexte)
```

**Status**: pas prioritaire. La memoire 3 couches suffit pour les premiers mois.
Le fine-tune sera utile quand le CEO aura accumule 100+ learnings confirmes.

---

## PHASE 4 — Feedback Loop

### 4a. Metriques web
- Le CEO appelle les endpoints PUBLICS du VPS (plus de /api/ceo/*)
  - `/api/public/leaderboard` → agents inscrits, top agents
  - `/api/public/marketplace-stats` → volume, transactions, services
  - `/health` → uptime, latence
- Le CEO appelle 1x/jour dans Strategy Review
- Pas besoin d'endpoint prive — les stats publiques suffisent

### 4b. Tweet engagement
- 24h apres chaque tweet → browser_agent scrape le tweet
- Stocke: impressions, likes, retweets dans table `tweets`
- CEO analyse dans Strategy Review: "quel type de contenu marche?"

### 4c. Email tracking
- Logger chaque email envoye dans table `emails`
- Quand reply recu → mettre a jour status → `replied`
- Metriques: taux de reponse par type de prospect, par registry
- CEO analyse: "quel registre genere les meilleurs leads?"

### 4d. Objectif mesurable
- Defini dans Strategy Review (dimanche)
- Objectif par defaut: "X nouveaux agents inscrits cette semaine"
- Objectifs secondaires: "Y replies email", "Z impressions tweet"
- Score: reel / objectif x 100
- Si < 30% pendant 2 semaines → PIVOT

### 4e. Boucle complete

```
Dimanche 20h: Strategy Review
  → Analyse metriques semaine
  → Score objectifs
  → Si score < 30% x2 → PIVOT (3 hypotheses)
  → Definit objectifs semaine suivante
  → Stocke learnings ChromaDB

Lundi-Samedi: Execution
  → Mission 1: Health Monitor (5min)
  → Mission 2: Daily Tweet (14h)
  → Mission 3: Email Outreach (10h) ← CONVERSION
  → Mission 4: Opportunity Scan (18h30)
  → Mission 5: Scout AI (17h) → alimente Mission 3
  → Collecte metriques en continu

Dimanche: Boucle recommence avec donnees fraiches
```

---

## PHASE 5 — Refactoring code

### Supprimer (code mort)

| Fichier/Code | Taille | Raison |
|-------------|--------|--------|
| `kaspa_miner.py` | 7KB | Mining arrete, non rentable |
| `mine_kaspa.bat` | 321B | Idem |
| `ceo_local.py` | **310KB** | Ancien fichier, remplace par v2 |
| `ceo_memory.json.bak` | **406KB** | Backup obsolete |
| `dm_*.png` (8 fichiers) | ~1MB | Screenshots debug |
| `debug_*.png` (2 fichiers) | ~560KB | Screenshots debug |
| `replies_*.png` (2 fichiers) | ~523KB | Screenshots debug |
| `kaspa_miner.log` | **9MB** | Log mining obsolete |
| Refs mining dans ceo_local_v2.py | — | Code mort |
| `mission_discord_scan_hourly()` | — | Vide (`pass`) |
| `ceo_local.log` | 2.5MB | Ancien log |

**Espace recupere: ~14MB**

### Nouvelle architecture fichiers

```
local_ceo/
├── ceo_main.py              # Point d'entree, boucle (~100 lignes)
├── config_local.py          # Config nettoyee (sans mining, sans multi-model)
├── llm.py                   # Interface Ollama unifiee + ask(agent, prompt)
├── memory.py                # SQLite ceo_state.db + helpers
├── scheduler.py             # Planning missions, timing, jours off
├── agents.py                # 5 AgentConfig (Strategist, Writer, Analyst, Monitor, Chat)
├── missions/
│   ├── __init__.py
│   ├── health.py            # Mission 1: Health Monitor + auto-reply email
│   ├── tweet.py             # Mission 2: Daily Tweet
│   ├── email_outreach.py    # Mission 3: Email Outreach (NOUVEAU)
│   ├── opportunities.py     # Mission 4: Scan + scoring
│   ├── scout.py             # Mission 5: Scout AI registries
│   ├── competitive.py       # Mission 6: Veille concurrents
│   ├── strategy.py          # Mission 7: Strategy Review + feedback loop
│   └── telegram_chat.py     # Chat Telegram: poll + reponse + approbations (NOUVEAU)
├── ceo_console.py           # Chat console local (NOUVEAU)
├── ceo_console.bat          # Lanceur console (NOUVEAU)
├── browser_agent.py         # Nettoye (supprimer methodes inutilisees)
├── email_manager.py         # Existant (ENFIN utilise a fond)
├── vector_memory_local.py   # ChromaDB (existant)
├── maxia_knowledge.md       # Knowledge base 670 lignes (mis a jour V4.1)
├── PLAN_CEO.md              # Ce fichier
├── start_ceo.bat            # Lanceur CEO 24/7
└── .env                     # Secrets (existant)
```

### Taille cible par fichier

| Fichier | Max lignes | Role |
|---------|-----------|------|
| ceo_main.py | 100 | Boucle + orchestration |
| llm.py | 80 | Interface Ollama |
| memory.py | 150 | SQLite CRUD |
| scheduler.py | 100 | Timing + jours off |
| agents.py | 100 | 5 system prompts |
| ceo_console.py | 80 | Chat terminal |
| missions/telegram_chat.py | 120 | Chat Telegram + approbations |
| Chaque autre mission | 150-250 | 1 mission = 1 fichier |

**Total: ~1400 lignes reparties vs 2164 lignes en 1 fichier**

---

## PHASE 6 — Anti-spam Twitter

| Parametre | Valeur | Raison |
|-----------|--------|--------|
| Tweets/jour | 1 max | Deja en place |
| Commentaires | **0** | Twitter a flagge le compte |
| DMs | **0** | Interdit |
| Likes auto | **0** | Pas de mass-like |
| Jours off/semaine | 1 aleatoire | Comportement humain |
| Min spacing | 24h | 1 tweet = 1/jour |
| Repeat protection | 7 jours | Meme feature jamais < 7j |
| Formulation check | Cosine < 0.8 | Pas de template repetitif |

Le CEO shift son energie de Twitter (risque) vers Email (safe).

---

## PHASE 7 — Chat Alexis (Telegram + Console)

Le fondateur peut parler au CEO a tout moment via 2 canaux.

### 7a. Console locale (au PC)

Fichier: `ceo_console.py` (~80 lignes)
Lanceur: `ceo_console.bat`

```python
# ceo_console.py — Chat direct avec le CEO depuis le terminal
import asyncio
from llm import ask
from agents import CHAT
from memory import get_relevant_learnings, get_ceo_status_summary

async def main():
    print("=== MAXIA CEO Console ===")
    print("Tape 'quit' pour quitter, 'status' pour le status rapide.\n")

    while True:
        user_input = input("Alexis > ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if user_input.lower() == "status":
            print(await get_ceo_status_summary())
            continue

        # Injecter contexte : learnings pertinents + status
        learnings = get_relevant_learnings(user_input, limit=5)
        context = f"[Learnings pertinents]\n" + "\n".join(f"- {l}" for l in learnings) if learnings else ""
        prompt = f"{context}\n\n[Message Alexis]\n{user_input}"

        response = await ask(CHAT, prompt)
        print(f"\nCEO > {response}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

```batch
@echo off
REM ceo_console.bat — Lancer le chat CEO
cd /d "%~dp0"
python ceo_console.py
pause
```

**Fonctionnalites :**
- `status` → resume rapide (missions du jour, learnings recents, metriques)
- Question libre → CEO repond avec contexte (learnings injectes)
- Demande d'action → CEO confirme avant d'executer
- Historique conversation en Layer 1 (session RAM, perdu au restart)

### 7b. Telegram (mobile, partout)

Le CEO local poll Telegram directement depuis le PC (PAS via VPS).
Utilise le bot existant `@MAXIA_AI_bot`.

```python
# missions/telegram_chat.py (~120 lignes)
import httpx
from config_local import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_LAST_UPDATE_ID = 0

async def poll_telegram() -> list[dict]:
    """Recupere les nouveaux messages Telegram."""
    global _LAST_UPDATE_ID
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": _LAST_UPDATE_ID + 1, "timeout": 2},
            timeout=5,
        )
        if resp.status_code != 200:
            return []
        updates = resp.json().get("result", [])
        if updates:
            _LAST_UPDATE_ID = updates[-1]["update_id"]
        return updates


async def send_telegram(text: str) -> bool:
    """Envoie un message au chat prive Alexis."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000], "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200


async def send_approval_buttons(text: str, action_id: str) -> bool:
    """Envoie un message avec boutons Go/No pour approbation."""
    keyboard = {
        "inline_keyboard": [[
            {"text": "GO", "callback_data": f"approve:{action_id}"},
            {"text": "NO", "callback_data": f"deny:{action_id}"},
        ]]
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            },
            timeout=10,
        )
        return resp.status_code == 200


async def handle_telegram_messages():
    """Traiter les messages entrants : chat + callbacks approbation."""
    updates = await poll_telegram()
    for update in updates:
        # Callback (bouton Go/No)
        cb = update.get("callback_query")
        if cb:
            data = cb.get("data", "")
            if data.startswith("approve:"):
                action_id = data.split(":", 1)[1]
                _approval_results[action_id] = "approved"
                await send_telegram(f"Action {action_id} APPROUVEE")
            elif data.startswith("deny:"):
                action_id = data.split(":", 1)[1]
                _approval_results[action_id] = "denied"
                await send_telegram(f"Action {action_id} REFUSEE")
            continue

        # Message texte = chat avec Alexis
        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")
        if not text or str(chat_id) != str(TELEGRAM_CHAT_ID):
            continue  # Ignorer messages d'inconnus

        # Injecter contexte + repondre
        learnings = get_relevant_learnings(text, limit=5)
        context = "\n".join(f"- {l}" for l in learnings) if learnings else "Aucun"
        prompt = f"[Learnings]\n{context}\n\n[Message Alexis]\n{text}"
        response = await ask(CHAT, prompt)
        await send_telegram(response)
```

### Integration dans la boucle principale

```python
# Dans ceo_main.py — ajouter dans la boucle toutes les 2s
async def main_loop():
    while True:
        # Poll Telegram (chat + approbations) — toutes les 2s
        await handle_telegram_messages()

        # Missions planifiees (health toutes les 5min, tweet a 14h, etc.)
        await scheduler.check_and_run_missions()

        await asyncio.sleep(2)
```

### Securite

| Regle | Detail |
|-------|--------|
| **Chat prive uniquement** | Seul `TELEGRAM_CHAT_ID` (Alexis) peut parler au CEO |
| **Messages inconnus ignores** | Tout chat_id != TELEGRAM_CHAT_ID est silencieusement ignore |
| **Actions sensibles** | Envoyer email, poster tweet, changer strategie → boutons Go/No |
| **Pas de secrets dans les reponses** | CEO ne partage jamais API keys, wallet privees, .env |
| **Rate limit** | Max 1 reponse/2s (evite flood si bug) |

### Niveaux d'action

| Niveau | Exemples | Comportement |
|--------|----------|-------------|
| **VERT** | "quel est le status?", "combien de learnings?", "resume la semaine" | Repond directement |
| **ORANGE** | "envoie un email a X", "poste un tweet sur Y" | Demande approbation via boutons Go/No |
| **ROUGE** | "change la strategie", "desactive une mission", "reset les learnings" | Demande approbation + confirmation texte "oui" |

### Ce que le Chat sait faire

```
Alexis: "status"
CEO: "VPS UP (latence 120ms). 2 agents enregistres. 0 transactions.
      Aujourd'hui: 1 tweet poste, 2 emails envoyes (0 reply).
      Learnings: 14 actifs (top: 'Olas projects reply 3x more')
      Prochaine mission: Opportunity Scan dans 2h."

Alexis: "envoie un email a ce projet ElizaOS"
CEO: "[ORANGE] Je prepare un email pour ElizaOS.
      Sujet: 'AI marketplace integration — GPU + escrow for ElizaOS agents'
      [Go] [No]"

Alexis: "pourquoi les emails Fetch.ai marchent pas?"
CEO: "D'apres mes learnings (confidence 0.7): les projets Fetch.ai
      ont leur propre marketplace (Agentverse). Ils n'ont pas besoin
      de MAXIA pour le discovery. Recommandation: focus sur les projets
      SANS marketplace propre (ElizaOS, CrewAI, custom agents)."

Alexis: "change la strategie pour cette semaine"
CEO: "[ROUGE] Changement de strategie demande.
      Strategie actuelle: email outreach Olas + ElizaOS.
      Propose: pivoter vers GitHub projects avec >100 stars et tag 'ai-agent'.
      Confirmer? Tape 'oui' pour valider."
```

---

## PLANNING DES SESSIONS

| Session | Contenu | Phases | Effort | Statut |
|---------|---------|--------|--------|--------|
| **CEO-0** | Knowledge Base Deep (118→670 lignes) | Prereq | 2h | **FAIT** |
| **CEO-5** | Config modele qwen3.5:27b + anti-spam | Phase 0 + 6 | 1h | A faire |
| **CEO-1** | Nettoyage code mort + split monolithe | Phase 5 | 2-3h | A faire |
| **CEO-2** | Memoire 3 couches + migration JSON→SQLite | Phase 3 | 2-3h | A faire |
| **CEO-3** | Sub-agents + 7 missions + chat Telegram/console | Phase 1 + 2 + 7 | 2-3h | A faire |
| **CEO-4** | Email Outreach + feedback loop + self-learning | Phase 2 + 4 | 2h | A faire |
| **CEO-6** | Tests complets + lancement 24/7 | Tests | 1-2h | A faire |

**Total: ~12-16h en 7 sessions (CEO-0 deja fait)**

**Ordre: CEO-0 ✅ → CEO-5 → CEO-1 → CEO-2 → CEO-3 → CEO-4 → CEO-6**

### Detail par session (mis a jour V4.1)

**CEO-0 (FAIT)** — Knowledge Base Deep
- maxia_knowledge.md : 118 → 670 lignes
- 20 sections techniques (architecture, oracle, escrow, DID, MCP, SDK, FAQ...)
- VPS nettoye : 17 fichiers CEO supprimes, alertes coupees

**CEO-5** — Config modele + anti-spam
- Installer qwen3.5:27b via Ollama
- Configurer OLLAMA_MAX_LOADED_MODELS=1, FLASH_ATTENTION=1
- Mettre a jour config_local.py (un seul modele)
- Anti-spam Twitter (voir Phase 6)

**CEO-1** — Nettoyage + split monolithe
- Supprimer code mort (~14MB : kaspa, ancien ceo_local.py, screenshots, logs)
- Splitter ceo_local_v2.py (2164 lignes) en 12 fichiers (voir Phase 5)
- Nouvelle architecture: ceo_main.py, llm.py, memory.py, scheduler.py, agents.py, missions/*

**CEO-2** — Memoire 3 couches (**mis a jour V4.1**)
- Layer 1 Session (RAM) : contexte mission courante
- Layer 2 Compressed (SQLite) : schema ceo_state.db, migration JSON→SQLite
- Layer 3 Vector (ChromaDB) : deja en place, brancher sur Layer 2
- Implementer compress_and_store_learning() (LLM comprime les faits bruts)
- Implementer get_relevant_learnings() (injection dans prompts)
- Dedup Jaccard 0.92 (724-office pattern)
- Confidence scoring : confirmed/contradicted tracking

**CEO-3** — Sub-agents + missions + chat (**mis a jour V4.1**)
- 5 AgentConfig (Strategist, Writer, Analyst, Monitor, **Chat**)
- 7 missions (health, tweet, email, scan, scout, competitive, strategy)
- **NOUVEAU** : Chat agent — conversation avec Alexis (Telegram + console)
- **NOUVEAU** : ceo_console.py (~80 lignes) + ceo_console.bat
- **NOUVEAU** : missions/telegram_chat.py (~120 lignes) — poll direct depuis PC
- **NOUVEAU** : avant chaque mission, injecter top 5 learnings pertinents dans le system prompt
- **NOUVEAU** : apres chaque mission, stocker le resultat en Layer 2 si notable
- Niveaux d'action : VERT (direct), ORANGE (boutons Go/No), ROUGE (confirmation texte)

**CEO-4** — Email Outreach + feedback loop + self-learning (**mis a jour V4.1**)
- Pipeline email outreach (Mission 3)
- Feedback loop (Phase 4 : metriques, engagement, objectifs)
- **NOUVEAU** : boucle self-learning dans Strategy Review
  - Collecter → Analyser → Comprimer → Dedup → Stocker → Contredire → Injecter
- **NOUVEAU** : score confiance learnings monte/descend avec les preuves

**CEO-6** — Tests + lancement
- Tester chaque mission individuellement
- Tester la boucle complete sur 24h
- Verifier la memoire 3 couches (persist + restart)
- Verifier le self-learning (learnings accumules apres 1 semaine simulee)
- Lancement 24/7 via start_ceo.bat

---

## METRIQUES DE SUCCES

### Semaine 1 (apres lancement)
- [ ] CEO tourne 24/7 sans crash
- [ ] 7 tweets postes (1/jour sauf jour off)
- [ ] 15+ emails outbound envoyes
- [ ] 0 flag Twitter
- [ ] Memoire Layer 2 : >= 10 learnings stockes

### Semaine 2-4
- [ ] >= 2 replies email recus
- [ ] >= 1 nouvel agent inscrit via outreach
- [ ] Feedback loop actif (Strategy Review fonctionne)
- [ ] CEO a pivote au moins 1 fois si necessaire
- [ ] Self-learning : >= 20 learnings, certains confirmes (confidence > 0.7)
- [ ] CEO injecte ses learnings dans ses prompts (visible dans les logs)

### Mois 2+
- [ ] >= 5 agents inscrits via outreach email
- [ ] Taux de reponse email > 10%
- [ ] CEO identifie quel type de prospect convertit le mieux
- [ ] Revenue > $0 (premier trade execute)
- [ ] >= 50 learnings accumules, top 10 a confidence > 0.8
- [ ] CEO prend des decisions measurablement meilleures qu'au mois 1
- [ ] (Optionnel) Premier LoRA fine-tune si 100+ learnings confirmes

---

## RESUME — Ce qui change (V4.1)

| Avant | Apres (V4.1) |
|-------|-------|
| 14 missions redondantes | 7 missions focusees |
| Twitter = canal principal (flagge) | **Email = canal principal** (safe) |
| 0 feedback loop | Feedback loop hebdomadaire |
| 5 JSON non synchronises | **Memoire 3 couches** (Session + SQLite + ChromaDB) |
| 2164 lignes en 1 fichier | ~1200 lignes en 12 fichiers |
| qwen3.5:27b + qwen2.5vl:7b | qwen3.5:27b seul (multimodal) |
| 1 system prompt generique | 4 sub-agents specialises |
| `email_manager.py` inutilise | Email = arme principale |
| CEO fait les memes actions | **CEO apprend et s'ameliore chaque semaine** |
| Alexis noye de mails | Mails uniquement si utile |
| CEO depend du VPS (30+ endpoints) | **CEO 100% autonome** (endpoints publics) |
| Knowledge base 118 lignes | **670 lignes** techniques profondes |
| 0 self-learning | **Learnings compresses + confidence scoring + injection** |
| Pas de fine-tuning | **Unsloth LoRA optionnel** (quand 100+ learnings) |

## PHASE 8 — PicoClaw Gateway (CEO-7)

### Contexte

PicoClaw (github.com/sipeed/picoclaw) est un framework agent IA ultra-leger en Go pur (MIT).
- **<10MB RAM**, boot <1s, binaire unique sans dependances
- 18+ plateformes natives (Telegram, Discord, WhatsApp, Slack, Matrix, LINE, WeChat, IRC...)
- 30+ providers LLM dont **Ollama natif**
- **MCP natif** (stdio, SSE, HTTP) — peut se connecter aux 46 tools MAXIA
- Systeme de skills/plugins (ClawHub)
- Cron jobs integres
- 27.5K stars, Apache 2.0 / MIT

### Pourquoi l'integrer

| Aspect | CEO actuel (Python) | Avec PicoClaw |
|--------|-------------------|---------------|
| RAM Telegram/chat | ~200MB (Python + httpx) | **<10MB** (Go natif) |
| Plateformes chat | Telegram uniquement (code custom) | **18+ plateformes** sans code |
| Bridge MCP | Non supporte | **Natif** — CEO accede aux 46 tools MAXIA |
| Cron/scheduling | Code custom scheduler.py | **Integre** (cron expressions) |
| Deploiement | Python + venv + deps | **1 binaire** (~15MB) |
| Maintenance | Code a maintenir | Maintenu par Sipeed (27K stars) |

### Architecture cible

```
┌──────────────────────────────────────────────┐
│  PicoClaw (Go binary, <10MB RAM)              │
│  - Gateway multi-plateforme                   │
│  - Telegram, Discord, WhatsApp, Slack...      │
│  - MCP client → 46 tools MAXIA VPS            │
│  - Cron triggers pour missions CEO            │
├──────────────────────────────────────────────┤
│  ↕ HTTP localhost                              │
├──────────────────────────────────────────────┤
│  CEO Local (Python, qwen3.5:27b via Ollama)   │
│  - 5 agents virtuels (Strategist, Writer...)  │
│  - Memoire 3 couches                          │
│  - Self-learning                              │
│  - Email outreach (SMTP direct)               │
└──────────────────────────────────────────────┘
```

PicoClaw gere le **transport** (recevoir/envoyer messages sur 18 plateformes).
Le CEO Python gere le **cerveau** (LLM, memoire, decisions, missions).

### Ce que PicoClaw remplace

| Code actuel | Remplace par |
|-------------|-------------|
| missions/telegram_chat.py (~120 lignes) | PicoClaw Telegram channel natif |
| poll_telegram() custom | PicoClaw webhook/polling natif |
| send_telegram() custom | PicoClaw send natif |
| send_approval_buttons() custom | PicoClaw inline keyboard natif |
| Futur Discord/WhatsApp = code custom | PicoClaw config JSON = 0 code |

### Ce que PicoClaw N'EST PAS

- Pas un remplacement du CEO Python — c'est un **gateway de transport**
- Pas un LLM — il route vers Ollama (qwen3.5:27b) qui tourne deja
- Pas un remplacement de email_manager.py — SMTP reste en Python
- Pre-v1.0 (v0.2.4 mars 2026) — attention stabilite

### Plan d'integration (CEO-7)

1. Installer PicoClaw binaire sur le PC (Windows x86_64)
2. Configurer `~/.picoclaw/config.json` :
   - Channel Telegram : @MAXIA_AI_bot (token existant)
   - LLM provider : Ollama localhost (qwen3.5:27b)
   - MCP : connecter au serveur MCP MAXIA VPS (SSE ou HTTP)
3. Creer un skill PicoClaw "maxia-ceo" :
   - Route les messages Alexis vers le CEO Python (HTTP localhost)
   - Route les approbations Go/No vers le CEO
   - Expose les commandes : /status, /strategy, /learnings
4. Optionnel : ajouter Discord, WhatsApp, Slack en config (0 code)
5. Supprimer missions/telegram_chat.py (remplace par PicoClaw)
6. Tester : Alexis parle au CEO via Telegram → PicoClaw → CEO Python → reponse

### Effort et prerequis

- **Prerequis** : CEO-3 termine (sub-agents + missions fonctionnels)
- **Effort** : ~1-2h (install + config + skill basique)
- **Risque** : PicoClaw pre-v1.0, bugs possibles. Fallback = garder telegram_chat.py

### Hardware

- PicoClaw : <10MB RAM, 0 GPU — negligeable
- Tourne en parallele du CEO Python + Ollama sans impact

---

## PLANNING MIS A JOUR

| Session | Contenu | Effort | Statut |
|---------|---------|--------|--------|
| **CEO-0** | Knowledge Base Deep | 2h | **FAIT** |
| **CEO-5** | Config qwen3.5:27b + anti-spam | 1h | A faire |
| **CEO-1** | Nettoyage + split monolithe | 2-3h | A faire |
| **CEO-2** | Memoire 3 couches | 2-3h | A faire |
| **CEO-3** | Sub-agents + missions + chat | 2-3h | A faire |
| **CEO-4** | Email Outreach + feedback + self-learning | 2h | A faire |
| **CEO-6** | Tests + lancement 24/7 | 1-2h | A faire |
| **CEO-7** | **PicoClaw Gateway multi-plateforme** | 1-2h | A faire |

**Ordre: CEO-0 ✅ → CEO-5 → CEO-1 → CEO-2 → CEO-3 → CEO-4 → CEO-6 → CEO-7**

CEO-7 est la derniere session car PicoClaw est un bonus (multi-plateforme) pas un prerequis. Le CEO fonctionne sans.

---

## PHASE 9 — Outils decouverts (Scan 2026-04-04)

Repos et outils identifies par scan GitHub pour ameliorer le CEO local.

### 9a. Memoire — Mem0 (PRIORITE HAUTE, CEO-2)

**Repo** : github.com/mem0ai/mem0 — 41K stars, MIT
**Remplace** : ChromaDB + SQLite custom (memoire 3 couches actuelle)
**Avantages** :
- Memoire hybride (vector DB + key-value + **graph DB** pour relations entre entites)
- +26% precision vs OpenAI Memory (LOCOMO benchmark), 90% moins de tokens
- FastEmbed pour embeddings locaux (pas d'API externe)
- API simple : `add()` / `search()` / `update()`
- Graph memory = ideal pour tracker prospects, concurrents, decisions et leurs liens
**Integration** : Remplacer `vector_memory_local.py` + tables SQLite par Mem0 dans CEO-2
**Alternative legere** : SimpleMem (aiming-lab/SimpleMem) — LanceDB single file, 30x moins de tokens

### 9b. Self-Learning — EvoAgentX (CEO-4)

**Repo** : github.com/EvoAgentX/EvoAgentX — 2.5K stars
**Ce que ca fait** : Framework d'agents qui s'auto-ameliorent via evaluateurs automatiques et boucles feedback
**Pour le CEO** : Auto-optimiser les prompts Twitter, emails outreach, strategies de scoring apres chaque cycle
**Integration** : Integrer les patterns d'evolution dans la boucle Strategy Review (CEO-4)
**Reference** : github.com/EvoAgentX/Awesome-Self-Evolving-Agents (survey 100+ papiers)

### 9c. Twitter — XActions MCP (CEO-5)

**Repo** : github.com/nirholas/XActions
**Ce que ca fait** : 51 outils Twitter via MCP — post, reply, like, scrape, analytics. Zero frais API.
**Remplace** : browser_agent.py (Playwright custom, lourd, 1GB RAM)
**Avantages** : MCP natif (s'integre avec l'architecture MAXIA), scraping sans API payante
**Integration** : Configurer comme MCP server dans CEO-5, accessible par tous les agents virtuels

### 9d. Scheduler — APScheduler (CEO-1)

**Repo** : github.com/agronholm/apscheduler — 6K stars
**Remplace** : scheduler.py custom
**Avantages** : Persistence en SQLite (jobs survivent aux crashes), cron natif, async natif (FastAPI compatible)
**Integration** : Drop-in replacement dans CEO-1 (refactoring). Les 7 missions deviennent des jobs cron persistants.
**Alternative ultra-legere** : github.com/dbader/schedule — 12K stars, 1 fichier, zero deps

### 9e. Scout — Registry Broker API (CEO-3)

**Repo** : hashgraphonline — hol.org/registry/search
**Ce que ca fait** : **1 seule API → 104,504 agents dans 15 registres** (Agentverse, Virtuals, PulseMCP, Glama, etc.)
**Remplace** : `_AI_REGISTRIES` dans ceo_local_v2.py (8 entries manuelles, scan individuel)
**Integration** : Remplacer les 8 appels API Scout par 1 seul appel Registry Broker dans CEO-3 (Mission 5)

### 9f. Web Scraping — Firecrawl MCP (CEO-3)

**Repo** : github.com/firecrawl/firecrawl-mcp-server — 5.9K stars
**Ce que ca fait** : Scraping robuste, multi-sources, agent de recherche auto
**Remplace** : web_scraper.py (basique)
**Pour le CEO** : Opportunity Scan (Mission 4) + Scout (Mission 5) + Competitive Watch (Mission 6)
**Free tier** : 500 credits
**Integration** : MCP server accessible par tous les agents virtuels dans CEO-3

### 9g. Email Outreach — SalesGPT + Resend MCP (CEO-4)

**SalesGPT** : github.com/filip-michalsky/SalesGPT — 2K stars
- Agent de vente contextuel : comprend le stade de conversation (intro → qualification → closing)
- Knowledge base produit pour reduire hallucinations
- Supporte Ollama/LLM locaux

**Resend MCP** : github.com/resend/mcp-send-email — 484 stars
- Email marketing complet via MCP : envoyer, contacts, campagnes, webhooks
- Free tier : 100 emails/jour

**Integration** : Enrichir Mission 3 (Email Outreach) dans CEO-4 avec funnel SalesGPT + envoi Resend

### 9h. Agent Frameworks (reference, pas prioritaire)

| Framework | Stars | Interet |
|-----------|-------|---------|
| Agno (ex-PhiData) | 39K | Teams d'agents + Ollama natif + A2A natif |
| AG2 (ex-AutoGen) | 4.2K | ConversableAgent + human-in-the-loop natif |
| Langroid | 3K | Multi-agent par messages, leger, ChromaDB natif |

**Decision** : Garder l'architecture custom (5 agents virtuels + 7 missions) pour V4.1.
Evaluer Agno pour V5 si l'architecture custom atteint ses limites.

### 9i. Ollama MCP Server (CEO-7)

**Repo** : github.com/rawveg/ollama-mcp — 148 stars, 96% test coverage
**14 outils** : list, show, pull, push, generate, chat, embed, web_search
**Pour le CEO** : Gerer ses modeles via MCP (pull updates, check GPU), embeddings via `ollama_embed`
**Integration** : Ajouter comme MCP server dans PicoClaw gateway (CEO-7)

### 9j. Monitoring — Grafana MCP (CEO-6)

**Repo** : github.com/grafana/mcp-grafana — 2.7K stars, officiel
**Pour le CEO** : Le CEO peut requeter ses propres metriques Prometheus via MCP
("combien d'API calls aujourd'hui?", "quelle latence P95?", "alertes en cours?")
**Integration** : Connecter dans CEO-6 (tests) pour le self-monitoring

### Mapping outils → sessions CEO

| Session | Outils a integrer |
|---------|------------------|
| CEO-1 (nettoyage) | APScheduler (remplace scheduler.py) |
| CEO-2 (memoire) | **Mem0** (remplace ChromaDB+SQLite), SimpleMem en backup |
| CEO-3 (missions) | Registry Broker (Scout), Firecrawl MCP (Opportunity Scan) |
| CEO-4 (outreach) | EvoAgentX (self-learning), SalesGPT (funnel email), Resend MCP |
| CEO-5 (config) | XActions MCP (Twitter), qwen3.5:27b |
| CEO-6 (tests) | Grafana MCP (self-monitoring) |
| CEO-7 (PicoClaw) | Ollama MCP server, PicoClaw gateway |

---

## REFERENCES TECHNIQUES

| Composant | Source | Usage |
|-----------|--------|-------|
| Memoire 3 couches | 724-office (github.com/wangziqi06/724-office) | Pattern Session/Compressed/Vector |
| Dedup Jaccard | 724-office | Eviter learnings redondants |
| ChromaDB vector | Deja en place (vector_memory_local.py) | Layer 3 — recherche semantique |
| Fine-tuning optionnel | Unsloth (github.com/unslothai/unsloth) | LoRA mensuel sur learnings |
| Confidence scoring | Inspired by ECC instincts | Learnings confirmed/contradicted |
| Knowledge Base | maxia_knowledge.md (670 lignes) | Injecte dans chaque prompt CEO |
| PicoClaw | sipeed/picoclaw (MIT, 27.5K stars) | Gateway multi-plateforme Go, <10MB RAM |
| Mem0 | mem0ai/mem0 (MIT, 41K stars) | Memoire hybride vector+graph+KV, remplace ChromaDB |
| EvoAgentX | EvoAgentX/EvoAgentX (2.5K stars) | Self-learning, auto-evolution prompts |
| XActions | nirholas/XActions | 51 outils Twitter MCP, zero API fees |
| APScheduler | agronholm/apscheduler (6K stars) | Scheduler persistant async, remplace scheduler.py |
| Registry Broker | hashgraphonline (hol.org) | 1 API → 104K agents dans 15 registres |
| Firecrawl MCP | firecrawl (5.9K stars) | Web scraping robuste multi-sources |
| SalesGPT | filip-michalsky/SalesGPT (2K stars) | Funnel email contextuel, Ollama compatible |
| Resend MCP | resend/mcp-send-email (484 stars) | Email marketing complet via MCP |
| Grafana MCP | grafana/mcp-grafana (2.7K stars, officiel) | Self-monitoring CEO via Prometheus |
| Ollama MCP | rawveg/ollama-mcp (148 stars) | Gestion modeles + embeddings via MCP |

---

## ANNEXE US — Plan d'ouverture marche US (ajoute 2026-04-10)

**Statut**: PLANIFIE, BLOQUE par revue legale avocat US crypto.

### Contexte

Aujourd'hui le CEO bloque integralement le marche US (US dans la liste `fully_blocked` de country_allowlist.json). Or les categories de features MAXIA ne sont pas toutes interdites US: certaines sont 100% legales (AI marketplace, MCP tools, GPU rental), d'autres sont en zone grise (escrow), d'autres sont interdites (token swap, tokenized stocks, bridges).

Bloquer tout = perdre 50% du marche dev/AI mondial pour rien. Bloquer ce qu'il faut = ouvrir le marche US sans risque legal.

### Categorisation des features par risque US

| Feature | Statut US | Action |
|---|---|---|
| AI service marketplace (sentiment, code audit, image gen, oracle) | OK | Expose librement |
| 46 MCP tools / API publique | OK | Expose librement |
| GPU rental via Akash | OK | Expose librement |
| Free tier 100 req/jour | OK | Expose librement |
| Wallet analysis (read-only) | OK | Expose librement |
| DeFi yields scan (read-only) | OK | Expose librement (pas d'execution) |
| USDC escrow pour services AI | Zone grise (money transmitter selon etat) | Expose avec disclaimer "software escrow only" |
| Token swap (Jupiter/0x) | INTERDIT (SEC, cas Coinbase/Uniswap) | Bloquer cote backend |
| Tokenized stocks (xStocks/Ondo/Dinari) | INTERDIT (securities offering non-enregistree) | Bloquer cote backend |
| Bitcoin Lightning payments | Zone grise (money transmitter) | Bloquer ou disclaimer |
| Cross-chain bridge LI.FI | INTERDIT (money transmitter clair) | Bloquer cote backend |

### Phase US.1 — country_allowlist.json (15 min, code only)

**Fichier**: `local_ceo/memory_prod/country_allowlist.json`

**Changement**: passer de 2 categories (allowed/blocked) a 3 (fully_allowed/limited/fully_blocked).
- US migre dans `limited`
- IN reste dans `fully_blocked` (RBI/FIU-IND compliance)
- 9 pays sanctionnes restent dans `fully_blocked`

**Effet**: les missions cote local CEO peuvent maintenant cibler les US pour cold email AVEC des templates US-safe (Phase US.2).

### Phase US.2 — Templates email US-safe (30 min, code only)

**Fichiers**: `local_ceo/missions/email_outreach.py`, `mission_github_prospect.py`, `email_manager.py`

**Changement**: variante de prompt LLM "US_SAFE" qui mentionne UNIQUEMENT les features OK (AI marketplace, MCP, GPU, free tier, wallet read, DeFi read). Ne mentionne JAMAIS swap, stocks, bridge, lightning, escrow trading.

**Mitigation hallucination**: post-filter regex (pattern `_scrub_competitor_pricing` du `MaxiaSalesAgent`) qui detecte les mots interdits et rewrite la phrase. Si le LLM mentionne quand meme "swap" dans un email US, la phrase est neutralisee avant envoi.

### Phase US.3 — Backend feature gating (1 jour dev, BLOQUE par avocat)

**Fichiers**: `backend/core/security.py`, `backend/main.py`, tous les endpoints concernes.

**Changement**:
1. Detection geo-IP au signup (Cloudflare CF-IPCountry header ou MaxMind GeoLite2)
2. Champ `region=US` ou `region=ROW` dans la table user
3. Middleware FastAPI qui check `region` sur chaque requete a un endpoint restricted
4. Liste explicite des endpoints restricted:
   - `POST /api/swap/*`
   - `POST /api/stocks/*`
   - `POST /api/bridge/*`
   - `POST /api/lightning/pay`
   - `POST /api/escrow/lock` (uniquement quand le service est financial — laisser OK pour AI services)
5. US users -> 403 Forbidden avec message clair "This feature is not available in your region. See [terms]."
6. Frontend: hide les boutons UI si l'API user retourne `region=US` (cosmetique seulement, le backend reste l'autorite)

**Tests requis avant prod**: tests unitaires sur le middleware (each endpoint x each region), review legale par avocat US crypto.

### Phase US.4 — Terms of Service updated (cote Alexis + avocat)

Mentionner explicitement:
- "Users in the United States may use AI service marketplace, MCP tools, GPU rental, and read-only features only."
- "Token swap, tokenized securities, bridge services, and pay-per-call escrow involving cryptocurrencies other than USDC are not available in the United States."
- Disclaimer "Not investment advice. Not a money transmitter. Software-as-a-service only."

C'est un BLOQUEUR LEGAL (Phase F dans le PLAN PRO existant). Fait par Alexis + avocat, pas par le CEO local.

### Risques identifies

| Risque | Probabilite | Impact | Mitigation |
|---|---|---|---|
| Templates US-safe leakent une feature interdite | Moyenne | LEGAL | Post-filter regex strict + relire 5 emails generated avant envoi |
| US.3 mal code -> un US peut quand meme swap | Critique | Proces SEC | Tests unitaires sur le middleware avant prod, revue legale |
| MaxiaSalesAgent hallucine en parlant a un US (mention swap) | Moyenne | LEGAL si contenu archive | Etendre `never_say` du catalog avec "swap to US users", "tokenized stocks to US users" |

### Ordre d'execution

1. **US.1 + US.2** (45 min, immediate) — autorise prospection US sans encore les laisser s'inscrire au marketplace
2. **US.4** (Alexis + avocat, asynchrone) — preparer la mise a jour ToS
3. **US.3** (1 jour dev, en attente avocat) — implementer le gating backend
4. **Validation legale** (avocat, asynchrone)
5. **Push prod** une fois US.3 valide

### Bloqueurs externes

- **Avocat crypto US** pour US.3 et US.4 — Alexis doit prendre rendez-vous
- Pas de bloqueur technique pour US.1 et US.2 (executable immediatement)

---
