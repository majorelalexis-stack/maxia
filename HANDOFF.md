# HANDOFF — MAXIA Hub (2026-05-01 — session 5)

## État actuel

**VPS opérationnel. Tous les endpoints Hub fonctionnent.**
Commit : `a4e7abd` — working tree propre.

---

## Ce qui est FAIT (sessions 1-5)

### Hub complet — 393 tests ✅

| Bloc | Tests | État VPS |
|---|---|---|
| P1-P6 Hub core (registry/score/forum/lineage/will/live) | 201 | ✅ |
| R0-R4 Réputation (scout/invite/r1/r2/r3/r4) | 172 | ✅ |
| Legal pages (/terms/privacy/legal/trust/cgu) | 7 | ✅ |
| Score unifié R1+R2+R3+R4 | 13 | ✅ |
| **Total** | **393** | **0 échec** |

### Fixes session 5

| Fix | Cause | Fichier |
|---|---|---|
| Hub 500 sur VPS | `create_database()` créait un nouveau singleton non partagé | `database.py` |
| Score R1+R2+R3+R4 intégrés | Boosts additifs non câblés dans `hub_score.py` | `hub_score.py` |
| Scheduler scout/r1 | Tâches hebdo/quotidiennes manquantes | `scheduler.py` |
| Escrow 15 chains → 2 chains | Claim faux dans marketplace.html | `marketplace.html` |

### Infrastructure

- Routes légales : `/terms` `/privacy` `/legal` `/trust` `/cgu` (301→/terms)
- Clause corpus AI : `terms.html` section 15, ancre `#corpus`
- Footer légal : `identity.html` + `marketplace.html`
- CLAUDE.md global : table routage `subagent_type`

---

## Reste à faire (2 items seulement)

| # | Action | Qui | Bloquant |
|---|---|---|---|
| M1 | **0% commission contrats** — Solana PDA + Base Solidity | Alexis (Remix + Anchor) | Oui — lancement |
| H4 | EAS schema Base mainnet → `EAS_MAXIA_SCHEMA_ID` dans `.env` VPS | Alexis (wallet ETH Base requis) | Non — optionnel R3 |

---

## Vérification VPS (2026-05-01)

```
/health              → {"status":"ok","db":"ok"}  ✅
/terms               → 200                         ✅
/cgu                 → 301 → /terms               ✅
/api/hub/scout/status → {"total_unverified":0}    ✅
/api/hub/leaderboard → {"leaderboard":[],"count":0} ✅
```

---

## H4 — Comment déployer le schema EAS

Script prêt : `scripts/deploy_eas_schema.js`

```bash
cd "C:\Users\Mini pc\Desktop\MAXIA V12"
npm install @ethereum-attestation-service/eas-sdk ethers
PRIVATE_KEY=0x<ta_clé_Base> node scripts/deploy_eas_schema.js
# → imprime EAS_MAXIA_SCHEMA_ID=0x...
# → SSH VPS : echo 'EAS_MAXIA_SCHEMA_ID=0x...' >> /opt/maxia/backend/.env
# → systemctl restart maxia
```

Schema : `address agent, string did, uint256 score, bool verified` (revocable).
R3 fonctionne sans ce schema (validation désactivée tant que vide).

---

## Prochaine action

Alexis : mettre à jour les smart contracts (0% commission) sur Remix (Base) et Anchor (Solana).
