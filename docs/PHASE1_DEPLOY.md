# Phase 1 — CEO Local auto-repond partout (deploy)

**Objectif** : le CEO Local repond automatiquement aux users sur Discord (#ask-ai), Forum, et Inbox via une seule mission poll-and-reply.

**Architecture**

```
User message sur n'importe quel canal
          │
          ▼
    ┌────────────┐
    │    VPS     │  ← ceo_bridge.py stocke dans ceo_pending_replies
    │  (buffer)  │
    └─────┬──────┘
          │
          │ CEO Local poll every 30s
          │ GET /api/ceo/messages/pending  (X-CEO-Key)
          ▼
    ┌────────────┐
    │ CEO Local  │  ← vps_bridge.py appelle qwen3.5:27b
    │  (brain)   │     via llm.llm() avec system prompt public
    └─────┬──────┘
          │
          │ POST /api/ceo/messages/{msg_id}/reply
          ▼
    ┌────────────┐
    │    VPS     │  ← _dispatch_discord / _dispatch_forum
    │ (dispatch) │
    └─────┬──────┘
          │
          ▼
    User voit la reponse
```

## 1. Fichiers modifies/crees

### Nouveaux fichiers
| Fichier | Role |
|---------|------|
| `backend/ceo_bridge.py` | Router FastAPI + ingest_message() + dispatchers |
| `backend/integrations/discord_assistant.py` | Listener Discord Gateway (read-only) |
| `local_ceo/missions/vps_bridge.py` | Mission poll+LLM+reply |
| `tests/test_ceo_bridge.py` | 25 tests pytest (tous verts) |
| `docs/PHASE1_DEPLOY.md` | Ce fichier |

### Fichiers modifies
| Fichier | Changement |
|---------|-----------|
| `backend/core/database.py` | + migration v13 `ceo_pending_replies` table |
| `backend/main.py` | + mount `ceo_bridge` router + lifespan start/stop listener |
| `backend/routes/forum.py` | + hook fire-and-forget `_ceo_bridge_enqueue()` apres `create_post()` |
| `backend/.env.example` | + `DISCORD_ASSISTANT_TOKEN` et `DISCORD_ASK_AI_CHANNEL_ID` |
| `local_ceo/ceo_main.py` | + import et scheduling `mission_vps_bridge` (30s interval) |

## 2. Env vars requises

### VPS (`backend/.env`)

```bash
# Token du bot "MAXIA assistant" (different de DISCORD_BOT_TOKEN outreach)
DISCORD_ASSISTANT_TOKEN=<le token regenere depuis Developer Portal>

# Channel ID du #ask-ai (voir section 3 pour le recuperer)
DISCORD_ASK_AI_CHANNEL_ID=<id numerique 18-20 chiffres>
```

**IMPORTANT :** `CEO_API_KEY` doit deja etre present (utilise par PicoClaw). Si absent, le bridge retournera 503.

### CEO Local (`local_ceo/.env` ou `backend/.env` si partage)

```bash
# Ces deux-la doivent matcher le VPS
VPS_URL=https://maxiaworld.app
CEO_API_KEY=<meme valeur que cote VPS>
```

## 3. Configuration Discord

### 3.1 Activer Message Content Intent (CRITIQUE)

Sans ca, le bot recevra des messages avec `content=""` et ne pourra rien faire.

1. https://discord.com/developers/applications
2. Ouvrir l'app **MAXIA assistant**
3. Menu gauche → **Bot**
4. Section **Privileged Gateway Intents** :
   - ✅ **Message Content Intent** (OBLIGATOIRE)
   - ❌ PRESENCE Intent (pas besoin)
   - ❌ SERVER MEMBERS Intent (pas besoin)
5. **Save Changes**

### 3.2 Recuperer le channel ID de #ask-ai

1. Discord → **Parametres utilisateur** (engrenage en bas a gauche)
2. **Avance** (Advanced) → active **Mode developpeur**
3. Retour a MAXIA Community → clic droit sur **#ask-ai**
4. Menu → **Copier l'identifiant du salon** (Copy Channel ID)
5. Colle-le dans `DISCORD_ASK_AI_CHANNEL_ID=<ici>`

C'est un nombre de 18-20 chiffres, exemple : `1491812345678901234`.

### 3.3 Permissions du bot dans MAXIA Community

Dans Server Settings → Roles → `MAXIA assistant`, verifier :
- ✅ View Channels (au moins pour #ask-ai)
- ✅ Send Messages
- ✅ Embed Links
- ✅ Read Message History
- ✅ Use External Emojis (optionnel)
- ✅ Add Reactions (optionnel)

## 4. Migration DB

La migration v13 s'applique **automatiquement** au prochain demarrage du backend. Elle cree la table `ceo_pending_replies` avec les indexes necessaires.

**Pour verifier manuellement** (apres deploy) :
```bash
ssh ovh
cd ~/maxia/backend
psql $DATABASE_URL -c "\d ceo_pending_replies"
psql $DATABASE_URL -c "SELECT MAX(version) FROM schema_version"  # doit renvoyer >= 13
```

## 5. Deploy VPS

```bash
# Sur ton poste
cd "C:\Users\Mini pc\Desktop\MAXIA V12"
git add backend/ceo_bridge.py backend/integrations/discord_assistant.py \
        backend/main.py backend/core/database.py backend/routes/forum.py \
        backend/.env.example local_ceo/missions/vps_bridge.py \
        local_ceo/ceo_main.py tests/test_ceo_bridge.py docs/PHASE1_DEPLOY.md
git commit -m "feat(phase1): CEO Local auto-reply on Discord/Forum/Inbox via VPS bridge"
git push origin main

# Sur le VPS
ssh ovh
cd ~/maxia
git pull
cd backend
# Ajouter les 2 nouvelles env vars dans .env
nano .env  # DISCORD_ASSISTANT_TOKEN=... ; DISCORD_ASK_AI_CHANNEL_ID=...
# Redemarrer
sudo systemctl restart maxia-backend
sudo journalctl -u maxia-backend -f --lines=100
```

Verifier dans les logs au boot :
```
[Phase1] CEO Bridge monte — /api/ceo/messages/*
[discord_assistant] listener started (channel_id=1491...)
[discord_assistant] Gateway connected
[discord_assistant] READY — bot user=... username=MAXIA assistant
```

## 6. Redemarrer CEO Local

```bash
# Sur ton PC
# Tue le process courant (Ctrl+C dans la fenetre CEO ou Task Manager)
cd "C:\Users\Mini pc\Desktop\MAXIA V12\local_ceo"
start_ceo.bat
```

Au boot tu dois voir :
```
═══════════════════════════════════════
  MAXIA CEO Local V3 — demarrage
  Modele: qwen3.5:27b
  ...
```

Puis toutes les 30 secondes :
```
[vps_bridge] polled N pending message(s)  # si queue non vide
```

## 7. Smoke test

### 7.1 Endpoint status (public, pas d'auth)

```bash
curl https://maxiaworld.app/api/ceo/messages/status
# { "bridge": "ceo_bridge", "version": "1.0", "channels": ["discord","email","forum","inbox"], "counters": {} }
```

### 7.2 Discord end-to-end

1. Va dans MAXIA Community → `#ask-ai`
2. Poste : `What is MAXIA?`
3. Attends 30-60 secondes
4. Le bot `MAXIA assistant` doit repondre en thread avec une reponse en anglais grounded sur llms-full.txt

**Si pas de reponse :**
- Verifier `journalctl -u maxia-backend` sur le VPS pour `[discord_assistant] ingested msg_...`
- Verifier la fenetre CEO Local pour `[vps_bridge] polled` et `[vps_bridge] msg=... REPLIED`
- Verifier la DB : `SELECT * FROM ceo_pending_replies ORDER BY received_at DESC LIMIT 5;`

### 7.3 Forum end-to-end

1. maxiaworld.app/forum → nouveau post dans n'importe quelle community
2. Titre : "How does escrow work?"
3. Attends 1 minute
4. Tu dois voir un reply `MAXIA Assistant` sous le post

### 7.4 Escalade sensitive

1. Discord `#ask-ai` → `I want a refund for my lost USDC`
2. Le bot ne doit PAS repondre automatiquement
3. Dans la DB : `SELECT status FROM ceo_pending_replies WHERE message LIKE '%refund%'` → doit etre `escalated`
4. TODO Phase 2 : envoyer un Telegram alert a Alexis pour traitement manuel

## 8. Troubleshooting

### Le bot Discord ne voit pas les messages
- Message Content Intent pas active dans Developer Portal (voir 3.1)
- Channel ID faux (recup via Developer Mode, voir 3.2)
- Bot pas dans le serveur → re-inviter via OAuth2

### Bridge retourne 503
- `CEO_API_KEY` manquant dans `backend/.env` VPS

### CEO Local poll mais 401
- `CEO_API_KEY` en local different de celui du VPS → synchroniser

### Reponses empty / hallucinees
- Verifier que `llms-full.txt` est bien present et complet
- Verifier que `memory_prod/capabilities_prod.json` existe et contient les capabilities reelles
- Regler `OLLAMA_NUM_CTX=8192` ou plus haut si tronque

### Boucle (bot repond a ses propres reponses)
- Les dispatchers `_dispatch_discord` et `_dispatch_forum` bypassent les hooks ingest (dispatch_discord envoie via REST API, dispatch_forum insere direct en DB) donc il ne devrait JAMAIS y avoir de boucle
- Si boucle observee : verifier que `forum._ceo_bridge_enqueue()` skip bien `author_wallet == "ceo_bridge"`

## 9. Limites connues (Phase 1)

- **PC off = silence total** (par decision explicite d'Alexis)
- **1 requete a la fois** (qwen3.5:27b mono-instance) — pas un probleme avec 0 clients
- **Escalade = stockage seulement**, pas d'alerte Telegram auto (Phase 2)
- **Email support** non connecte (Phase 2)
- **Chat widget website** non fait (Phase 3)
- **Forum replies entre users** ne declenchent PAS le bot (on ne hook que `create_post`, pas `create_reply`)
- **Bot Discord = read-only write uniquement dans #ask-ai**, ne repond pas dans les autres channels

## 10. Rollback

Si ca casse quelque chose :

```bash
# VPS — desactiver sans rollback complet
ssh ovh
cd ~/maxia/backend
# Commenter les 2 env vars dans .env → le listener se desactive au prochain restart
nano .env
# DISCORD_ASSISTANT_TOKEN=... → #DISCORD_ASSISTANT_TOKEN=...
sudo systemctl restart maxia-backend
```

Le bridge reste monte mais ne reçoit plus rien de Discord. La table `ceo_pending_replies` reste vide. Zero impact sur le reste.

Rollback complet :
```bash
git revert HEAD  # revert le commit Phase 1
git push
```

La migration v13 reste en place (la table ne gene pas).
