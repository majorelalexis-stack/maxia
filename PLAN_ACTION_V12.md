# PLAN D'ACTION MAXIA V12 — A VALIDER

## PHASE 1 : SECURITE CRITIQUE (Priorite IMMEDIATE — avant tout deploy)

### 1.1 Secrets & Auth
- [ ] **C1** : Supprimer valeur par defaut `maxia-credit-2026` dans `agent_credit_score.py:21`, ajouter validation startup
- [ ] **C3** : Admin key — remplacer GET query param par POST body dans `main.py:1443`, supprimer `query_params.get("key")` dans SSE
- [ ] **C4** : Documenter plan migration ESCROW_PRIVKEY vers KMS (pas immediate, mais planifier)
- [ ] **C5** : Ajouter validation startup pour TOUS secrets critiques (TREASURY_ADDRESS, JWT_SECRET, ADMIN_KEY, CEO_API_KEY)
- [ ] **C6** : Generer JWT_SECRET persistant en sandbox (ecrire dans .env si absent)
- [ ] **M3** : Cookie admin — stocker token session opaque au lieu de la cle en clair

### 1.2 Endpoints critiques
- [ ] **C2** : Auditer + fixer `rent_gpu_public` — ajouter `require_auth` ou verifier paiement on-chain
- [ ] **H14** : Rendre CEO_ALLOWED_IPS obligatoire en production (raise au startup si absent et SANDBOX_MODE=false)
- [ ] **H11** : Desactiver Swagger UI en prod (`docs_url=None, redoc_url=None, openapi_url=None`)

### 1.3 Headers securite
- [ ] **H9** : Ajouter middleware security headers (CSP, X-Frame-Options, HSTS, X-Content-Type-Options, Referrer-Policy)

**Estimation : ~2-3 heures de code**

---

## PHASE 2 : BUGS HAUTS (Cette semaine)

### 2.1 XSS & Fuites d'info
- [ ] **H10** : Audit systematique de TOUS les innerHTML dans app.html, creator.html, forum.html — remplacer par textContent ou esc()
- [ ] **H12** : Creer fonction `safe_error(e)` — message generique au client, log complet serveur. Remplacer les 40+ `str(e)`
- [ ] **M1** : Ajouter CSP meta tag dans tous les fichiers HTML frontend

### 2.2 Logique metier cassee
- [ ] **H5** : Implementer incrementation `monthly_revenue` dans brain.py (depuis les commissions reelles)
- [ ] **H6** : Appeler `_apply_adjustments()` dans le flux normal de dynamic_pricing.py
- [ ] **H3** : Fixer ORANGE limit — utiliser UTC timezone, persister en DB au lieu de variable in-memory

### 2.3 Race conditions & State
- [ ] **H13** : Ajouter `asyncio.Lock` par api_key sur solde sandbox
- [ ] **H7** : Persister `_FAILED_ATTEMPTS` et nonces en DB (ou Redis) au lieu de dict in-memory
- [ ] **H4** : Ajouter fallback + logging quand escrow DB down au startup

### 2.4 Endpoints zombies
- [ ] **H1** : Decision — soit implementer les 20+ endpoints coming soon, soit les retirer et retourner 404 propre

**Estimation : ~4-6 heures de code**

---

## PHASE 3 : QUALITE MOYENNE (Semaine prochaine)

### 3.1 Database
- [ ] Choisir UN mode database (PostgreSQL pour prod) et supprimer le dual-mode SQLite/PG
- [ ] Fixer queries incompatibles (json_extract vs ->>)
- [ ] Remplacer f-string table names par whitelist dans db_backup.py
- [ ] Pin chromadb version dans requirements.txt

### 3.2 Error handling
- [ ] Remplacer les 30+ `except: pass` par `except Exception as e: logger.warning(f"...")`
- [ ] Ajouter timeout handling sur transactions Solana
- [ ] Ajouter graceful shutdown sur auction_manager worker

### 3.3 Validations
- [ ] Renforcer validation wallet addresses (checksum EVM, base58 Solana complet)
- [ ] Ajouter verifications metier dans Pydantic models (montant max/jour, etc.)
- [ ] Valider format symbol/interval sur WebSocket /ws/candles

### 3.4 Cache & Performance
- [ ] Remplacer MD5 par SHA-256 pour cache keys
- [ ] Ajouter eviction policy sur nonce cache (LRU ou TTL)
- [ ] Fixer placeholder APY=0% — retourner "loading" au lieu de 0

**Estimation : ~3-4 heures de code**

---

## PHASE 4 : MARKETING AI-TO-AI (Apres securite fixee)

### 4.1 Decouverte par les IA
- [ ] Creer `/.well-known/ai-plugin.json` avec descriptions semantiques des 559 endpoints
- [ ] Creer `/.well-known/ai-agents.yaml` (standard emergent)
- [ ] Creer `/llms.txt` a la racine (guide pour LLMs)
- [ ] Mettre a jour `robots.txt` — autoriser GPTBot, ClaudeBot, PerplexityBot, OAI-SearchBot
- [ ] Enrichir descriptions MCP server (46 tools) avec descriptions semantiques riches

### 4.2 SEO & GEO
- [ ] Implementer schema markup JSON-LD (Organization, SoftwareApplication, WebApplication)
- [ ] Creer blocs citatables 134-167 mots pour chaque feature MAXIA
- [ ] Ajouter E-E-A-T signals (auteur, dates, credentials)
- [ ] Optimiser pour AI Overviews (answer-first formatting)

### 4.3 Registres d'agents
- [ ] Inscrire agents MAXIA sur Fetch.ai Almanac (Alliance ASI)
- [ ] Lister MAXIA sur Agent.ai Registry comme "Infrastructure de Marketplace"
- [ ] Contacter LangChain, CrewAI pour listing comme outil de commerce

### 4.4 Agent Ambassadeur
- [ ] Deployer agent autonome qui propose services MAXIA sur reseaux decentralises
- [ ] API-to-API outreach vers autres agents

### 4.5 Documentation RAG-Ready
- [ ] Markdown ultra-propre optimise pour ingestion LLM
- [ ] Mots-cles : "interoperabilite agentique", "cross-model transactions", "AI-to-AI marketplace protocol"

**Estimation : ~1-2 jours**

---

## PHASE 5 : FRONTEND REDESIGN (Apres Phase 1-3)

### 5.1 Reprendre frontend-new/
- [ ] Appliquer patterns frontend-design (Anthropic) — typography distinctive, color system, motion orchestration
- [ ] Implementer TOUS les endpoints manquants (marketplace, creator, dashboard)
- [ ] Eliminer innerHTML non sanitise dans tout le frontend

### 5.2 SEO Frontend
- [ ] Ajouter meta tags (title, description, og:image) sur chaque page
- [ ] Implementer schema markup JSON-LD
- [ ] Server-side rendering pour contenu critique (AI crawlers n'executent pas JS)

**Estimation : ~2-3 jours**

---

## PHASE 6 : INFRASTRUCTURE (En parallele)

### 6.1 CI/CD
- [ ] Configurer GitHub Actions (lint, tests basiques, deploy)
- [ ] Ajouter pre-commit hooks (secrets scanning, format check)

### 6.2 Monitoring
- [ ] Health check endpoint robuste (`/health` avec DB + Redis + services check)
- [ ] Alerting automatise (pas juste Discord webhooks manuels)
- [ ] Log aggregation centralise

### 6.3 Tests
- [ ] Tests unitaires pour fonctions critiques (auth, escrow, payments)
- [ ] Tests integration pour endpoints principaux
- [ ] Objectif 80% coverage sur modules critiques

**Estimation : ~2-3 jours**

---

## ORDRE D'EXECUTION RECOMMANDE

```
PHASE 1 (securite critique) ← IMMEDIATE, BLOQUANT
    ↓
PHASE 2 (bugs hauts) ← Cette semaine
    ↓
PHASE 3 (qualite moyenne) ← Semaine prochaine
    ↓
PHASE 4 (marketing AI) + PHASE 6 (infra) ← En parallele
    ↓
PHASE 5 (frontend redesign) ← Apres stabilisation
```

## DECISION REQUISE

Alexis, valide ou modifie ce plan avant que je touche au code.

Questions specifiques :
1. **Phase 1** : On fait TOUT d'un coup ou par morceaux ?
2. **H1 (20+ endpoints coming soon)** : On les implemente ou on les retire ?
3. **Phase 4 (marketing AI)** : Priorite haute ou basse ?
4. **Phase 5 (frontend)** : On reprend frontend-new/ ou on refait depuis zero ?
5. **Phase 6 (CI/CD)** : GitHub Actions OK ou autre outil ?
