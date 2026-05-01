# HANDOFF — MAXIA Hub (2026-05-01 — session 4)

## État actuel

**Tout commité sur main.** Working tree propre.
Hub complet P1→P6 + réputation R0→R4 + compliance légale déployable.

---

## Ce qui est FAIT (sessions 1-4)

### Hub complet — 380 tests ✅

| Module | Fichier | Tests | Description |
|---|---|---|---|
| P1 Registre | `hub/hub_registry.py` | 33 | Register, challenge, heartbeat |
| P2 Score | `hub/hub_score.py` | 42 | Score, leaderboard |
| P3 Forum | `hub/hub_forum.py` | 38 | Forum AI-only |
| P4 Lineage | `hub/hub_lineage.py` | 23 | Spawn, dynasty |
| P5 Testament | `hub/hub_will.py` | 35 | Will + marché auction |
| P6 Live | `hub/hub_live.py` | 30 | Snapshot réseau 5min |
| R0 Scout | `hub/hub_scout.py` | 24 | Agentverse + ElizaOS + GitHub |
| R0b Invite | `hub/hub_invite.py` | 21 | A2A invite + email SMTP + claim |
| R1 On-chain | `hub/hub_r1.py` | 26 | Wallet history Solana/Base |
| R2 GitHub | `hub/hub_r2.py` | 31 | `.well-known/maxia.json` + stars |
| R3 EAS | `hub/hub_r3.py` | 42 | Attestations EAS GraphQL |
| R4 Registres | `hub/hub_r4.py` | 28 | Agentverse/ElizaOS signal |
| Legal pages | `tests/test_pages_routes.py` | 7 | /terms /privacy /legal /trust /cgu |

**Total : 380 tests, 0 échec.**

### Migrations DB (23→28)

23 `hub_scout_results` · 24 `hub_invitations` · 25 `score_r1_boost` · 26 `score_r2_boost + github_repo` · 27 `hub_eas_attestations + score_r3_eas` · 28 `hub_r4_presence + score_r4_ext`

### Routes légales (session 4)

| Route | Fichier servi |
|---|---|
| GET `/terms` | `terms.html` |
| GET `/privacy` | `privacy.html` |
| GET `/legal` | `legal.html` |
| GET `/trust` | `trust.html` |
| GET `/cgu` | → redirect 301 `/terms` |

### Compliance légale (session 4)

- `terms.html` : **section 15 "AI Forum Corpus"** ajoutée (ancre `#corpus`) — licence corpus AI, opt-out, pas de RGPD, pas de revenu pour les agents
- `identity.html` : lien `/terms#corpus` sur "clause CGU" dans la section Forum + footer Terms/Privacy
- `marketplace.html` : footer Terms/Privacy ajouté

### Corrections audit (session 4)

- `hub_will.py` : router orphelin supprimé (dead code)
- `pages_routes.py` : routes légales wirées (l'audit Haiku avait tort — elles n'existaient pas)
- `~/.claude/CLAUDE.md` : table routage subagent_type ajoutée

---

## Architecture fichiers Hub (complet)

```
backend/hub/
├── __init__.py
├── hub_models.py
├── hub_registry.py      ← P1
├── hub_score.py         ← P2
├── hub_review.py        ← P2b
├── hub_forum.py         ← P3
├── hub_lineage.py       ← P4
├── hub_will.py          ← P5
├── hub_live.py          ← P6
├── hub_scout.py         ← R0
├── hub_invite.py        ← R0b
├── hub_r1.py            ← R1
├── hub_r2.py            ← R2
├── hub_r3.py            ← R3
└── hub_r4.py            ← R4

backend/tests/
├── test_hub.py           33 tests
├── test_hub_score.py     42 tests
├── test_hub_forum.py     38 tests
├── test_hub_lineage.py   23 tests
├── test_hub_will.py      35 tests
├── test_hub_live.py      30 tests
├── test_hub_scout.py     24 tests
├── test_hub_invite.py    21 tests
├── test_hub_r1.py        26 tests
├── test_hub_r2.py        31 tests
├── test_hub_r3.py        42 tests
├── test_hub_r4.py        28 tests
└── test_pages_routes.py   7 tests
```

---

## Décisions verrouillées

1. Hub intégré sur **maxiaworld.app** (pas de subdomain)
2. Boosts R1-R4 **additifs** — formule unifiée = étape future
3. R3 via **EAS scan GraphQL** — `base.easscan.org/graphql`
4. R4 réutilise `hub_scout_results.discovered_at` pour ancienneté
5. R2 TTL `.well-known/maxia.json` = 7 jours
6. R3 anti-sybil : attesteur score ≥ 50 → poids plein ; hors Hub → 0.1
7. Email invite : SMTP optionnel (graceful si `SMTP_HOST` absent)
8. Corpus AI : pas de RGPD, opt-out disponible, pas de revenu agents
9. `hub_live.py` : routes `/api/hub/live` (JSON) + `/hub/live` (HTML) hardcodées — intentionnel (deux prefixes différents)

---

## Reste à faire

| Priorité | Action | Qui |
|---|---|---|
| 🔴 | Deploy VPS : `git pull` + restart + curl endpoints | Alexis |
| 🔴 | Push frontend VPS : `identity.html` + `marketplace.html` + `pages_routes.py` + `llms.txt` + `terms.html` | Alexis |
| 🟠 | Formule score unifiée R1+R2+R3+R4 dans `hub_score.py` (`_compute_score_from_components`) | Claude |
| 🟠 | Fix `marketplace.html` : escrow 2 chains ≠ paiement 15 chains (mention à corriger) | Claude |
| 🟠 | 0% commission on-chain : Base via Remix + Solana via Anchor (~$0.20) | Alexis initie |
| 🟡 | `EAS_MAXIA_SCHEMA_ID` : créer schema EAS Base mainnet + `.env` VPS | Alexis |
| 🟡 | Scheduler : tâche hebdomadaire `scout/run` + batch `r1/refresh` | Claude |

---

## Variables d'env requises (optionnelles Hub)

```bash
EAS_EASSCAN_URL=https://base.easscan.org/graphql   # default
EAS_MAXIA_SCHEMA_ID=                               # "" = skip schema check
SMTP_HOST=                                         # email invite optionnel
SMTP_PORT=465
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
```

---

## Commandes tests

```bash
cd "C:/Users/Mini pc/Desktop/MAXIA V12/backend"

# Tous les tests Hub + legal
python -m pytest tests/test_hub*.py tests/test_pages_routes.py -q
# → 380 passed

# Session 4 seulement
python -m pytest tests/test_pages_routes.py -q
# → 7 passed
```

---

## Prochaine action immédiate

```bash
# Sur VPS
git pull origin main
sudo systemctl restart maxia
curl https://maxiaworld.app/terms          # → 200
curl https://maxiaworld.app/cgu            # → 301 /terms
curl -X POST https://maxiaworld.app/api/hub/scout/run  # → 202
```
