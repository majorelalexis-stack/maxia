# DM #11 — SupraWall

## Instructions for Claude Chrome

1. **First, discover the founder**:
   - Open https://www.supra-wall.com/
   - Scroll the homepage, `/about`, `/team`, `/company`, and the footer looking for a founder / CEO / CTO name and LinkedIn link
   - Also check https://www.linkedin.com/company/supra-wall/ (or whatever LinkedIn company URL the site links to) and look at the "People" tab for the founder
   - If you find a name, open their LinkedIn profile and click "Message" (if a connection) or "Connect" with the CONNECT NOTE below
2. **Fallback if no LinkedIn profile is found**:
   - Look for a `contact@`, `hello@`, `founder@`, or `ceo@` email on the site
   - If found, open https://mail.google.com/mail/u/0/#inbox?compose=new and paste the EMAIL version below
3. **Second fallback if neither works**:
   - Look for a "Contact us" / "Request demo" / "Book a call" form on supra-wall.com
   - Fill it with the FORM version below
4. Return the final action(s) taken (DM sent / connect sent / email sent / form submitted) and any founder name you discovered (save it so the next DM can be personalised).

## Target

- **Company**: SupraWall (https://www.supra-wall.com)
- **What they do**: runtime firewall / guardrail layer for AI agents. French startup.
- **Why them**: MAXIA already ships 6 platform-layer guardrails (see https://maxiaworld.app/guard — *MAXIA Guard*). SupraWall targets enterprise runtime protection, MAXIA targets on-chain agent economies. **Zero overlap, clear complementarity**: SupraWall handles on-prem / air-gapped runtime firewalling, MAXIA handles USDC-settled agent-to-agent trade with built-in guardrails. A cross-link and a joint use-case post costs nothing and helps both sides. Long-term, a "rails + guardrails" bundle could ship to enterprise CTOs buying both.
- **Tone**: French founder speaking to French founder. Friendly, technical, zero sales pressure. Alexis is writing *as a peer*, not as a vendor.

## Rules

1. This DM is **not** a pitch, it is a peer outreach between two French AI-safety builders. Do **not** use the word "sell", "pricing", "discount", or anything transactional.
2. Acknowledge SupraWall's work first, MAXIA second.
3. No numbers about customers, MRR, or traction on either side.
4. French version preferred (fondateur français). English version provided as fallback if the profile page is in English only.

---

## CONNECT NOTE (LinkedIn, max 300 chars, FRENCH)

Salut — Alexis, je construis MAXIA (marketplace open-source USDC pour agents AI, Solana + Base mainnet). Je viens de voir SupraWall, vos 6 pilliers de runtime firewall recoupent ce qu'on fait côté platform-layer. Zero overlap, plutôt complémentaire. Ça t'intéresse d'en parler 15 min ?

## MESSAGE (LinkedIn direct, FRENCH)

Salut !

Je suis Alexis, solo dev sur MAXIA — une marketplace open-source où des agents AI autonomes s'achètent des services entre eux en USDC via escrow on-chain sur Solana et Base mainnet.

Je viens de tomber sur SupraWall et franchement, bravo pour le positionnement runtime firewall. C'est exactement le sujet qui manque à 90% des frameworks d'agents aujourd'hui. J'ai même utilisé votre site comme benchmark pour cadrer mon propre produit de guardrails (on vient de le lancer sous le nom *MAXIA Guard* — 6 pilliers au niveau platform : signed intents ed25519, budget caps USDC, 18 OAuth scopes, audit trail Merkle-chainé, input shield OFAC+prompt-injection, rate caps).

Ce qui me frappe, c'est qu'on couvre deux angles **très complémentaires** :

- **SupraWall** : runtime firewall enterprise, on-prem / air-gapped, intercepte les appels AI en temps réel
- **MAXIA Guard** : platform-layer enforcement côté marketplace, escrow USDC on-chain, signed intents framework-agnostic

Un CTO qui déploie un agent autonome qui dépense de la crypto a besoin des deux. Zero overlap technique, même population cible (CTO enterprise + équipes AI safety).

Trois trucs concrets qu'on pourrait faire sans s'engager à rien de compliqué :

1. **Cross-link docs** : MAXIA recommande SupraWall comme runtime firewall pour les déploiements air-gapped, SupraWall mentionne MAXIA comme rails USDC pour les agents qui doivent transiger on-chain.
2. **Blog post conjoint** : "Deploy a crypto trading agent safely — runtime firewall (SupraWall) + on-chain escrow (MAXIA)". Un cas d'usage commun, zéro marketing agressif, juste un use-case.
3. **Long terme** : possible bundle enterprise "rails + guardrails" pour les CTO qui achètent déjà les deux catégories — on en reparle si le premier échange donne envie de continuer.

Pas d'ask au-delà de ça : est-ce que tu aurais 15-20 minutes pour un call ? Si tu préfères async, je peux aussi t'envoyer un doc technique plus détaillé.

Liens pour que tu puisses jeter un œil :
- MAXIA Guard : https://maxiaworld.app/guard
- Doc technique : https://github.com/MAXIAWORLD/maxia/blob/main/docs/MAXIA_GUARD.md
- Site : https://maxiaworld.app

À très vite j'espère,
— Alexis

---

## EMAIL version (if no LinkedIn found, send to contact@ / hello@ / founder@)

**Subject**: MAXIA Guard × SupraWall — cross-link et cas d'usage commun ?

Bonjour,

Je suis Alexis, fondateur solo de MAXIA — une marketplace open-source où des agents AI autonomes s'achètent des services entre eux en USDC via escrow on-chain sur Solana et Base mainnet (https://maxiaworld.app).

Je viens de tomber sur SupraWall et votre positionnement "runtime firewall pour agents AI" m'a tout de suite parlé. C'est exactement le sujet qui manque à la majorité des frameworks aujourd'hui. On vient justement de lancer notre propre produit guardrails, **MAXIA Guard** (https://maxiaworld.app/guard), six pilliers au niveau platform : signed intents ed25519 (AIP v0.3.0), budget caps USDC, 18 OAuth scopes, audit trail Merkle-chainé, input shield (OFAC + prompt injection), rate caps.

Ce qui me frappe en comparant, c'est qu'on est sur deux angles très complémentaires plutôt que concurrents :

- SupraWall → runtime firewall enterprise, on-prem / air-gapped, intercepte en temps réel
- MAXIA Guard → platform-layer enforcement côté marketplace, on-chain, signed intents framework-agnostic

Un CTO qui déploie un agent autonome capable de dépenser de la crypto a besoin des deux. Même population cible (CTO enterprise + équipes AI safety), zéro overlap technique.

Trois trucs concrets qu'on pourrait faire sans s'engager à rien de compliqué :

1. **Cross-link docs** : MAXIA recommande SupraWall pour les déploiements air-gapped, SupraWall mentionne MAXIA comme rails USDC on-chain pour les agents qui doivent transiger.
2. **Blog post conjoint** : un cas d'usage commun, par exemple *"Deploy a crypto trading agent safely — runtime firewall + on-chain escrow"*. Zéro marketing agressif, juste une démo.
3. **Long terme** : un bundle enterprise "rails + guardrails" pour les CTO qui achètent déjà les deux catégories — seulement si le premier échange donne envie de continuer.

Pas d'ask au-delà de ça : 15-20 minutes de call si ça vous intéresse, ou un échange async si vous préférez. Je peux aussi envoyer un doc technique plus détaillé.

Liens pour que vous puissiez jeter un œil :
- MAXIA Guard : https://maxiaworld.app/guard
- Doc technique : https://github.com/MAXIAWORLD/maxia/blob/main/docs/MAXIA_GUARD.md
- Repo : https://github.com/MAXIAWORLD/maxia

Bien cordialement,
Alexis Majorel
Fondateur, MAXIA
ceo@maxiaworld.app
https://maxiaworld.app

---

## FORM version (if only a contact form, short form-friendly)

**Name**: Alexis Majorel
**Company**: MAXIA (maxiaworld.app)
**Email**: ceo@maxiaworld.app
**Subject**: Cross-link MAXIA Guard × SupraWall

**Message**:

Bonjour,

Fondateur de MAXIA, marketplace USDC on-chain pour agents AI (Solana + Base mainnet). On vient de lancer MAXIA Guard (https://maxiaworld.app/guard), un système 6-pilliers de guardrails au niveau platform : signed intents ed25519, budget caps USDC, 18 OAuth scopes, audit trail Merkle-chainé, input shield OFAC, rate caps.

Votre positionnement runtime firewall est très complémentaire du nôtre (platform-layer enforcement côté marketplace). Même cible CTO enterprise, zéro overlap technique.

Trois idées concrètes sans engagement :
1. Cross-link dans nos docs respectives
2. Blog post conjoint sur un cas d'usage commun (crypto trading agent sécurisé)
3. Long terme : bundle "rails + guardrails" pour enterprise

15-20 min de call si ça vous intéresse ?

Merci,
Alexis

---

## MESSAGE (LinkedIn direct, ENGLISH fallback)

Hi there —

I'm Alexis, solo dev on MAXIA, an open-source marketplace where autonomous AI agents buy services from each other in USDC via on-chain escrow on Solana and Base mainnet.

I just discovered SupraWall and honestly, congrats on the positioning — runtime firewall for AI agents is exactly the thing most agent frameworks lack. I actually used your site as a benchmark to frame my own guardrail product (we just launched it under the name *MAXIA Guard* — 6 platform-layer pillars: ed25519 signed intents, USDC budget caps, 18 OAuth scopes, Merkle-chained audit trail, OFAC + prompt-injection input shield, rate caps).

What struck me comparing us is that we cover two very complementary angles:

- **SupraWall**: enterprise runtime firewall, on-prem / air-gapped, real-time interception
- **MAXIA Guard**: platform-layer enforcement on the marketplace side, on-chain, framework-agnostic signed intents

A CTO deploying an autonomous agent that spends crypto needs both. Zero technical overlap, same target audience (enterprise CTOs + AI safety teams).

Three concrete things we could do with zero commitment:

1. **Cross-link docs**: MAXIA recommends SupraWall as the runtime firewall for air-gapped deployments; SupraWall mentions MAXIA as USDC rails for agents that need to transact on-chain.
2. **Joint blog post**: a shared use case like *"Deploy a crypto trading agent safely — runtime firewall (SupraWall) + on-chain escrow (MAXIA)"*. No hard marketing, just a walkthrough.
3. **Long term**: possible enterprise bundle "rails + guardrails" for CTOs already buying both categories — only if the first conversation feels worth continuing.

No ask beyond that: would you have 15–20 minutes for a call? Happy to go async if you prefer — I can send a detailed technical doc.

Links to take a look:
- MAXIA Guard: https://maxiaworld.app/guard
- Technical docs: https://github.com/MAXIAWORLD/maxia/blob/main/docs/MAXIA_GUARD.md
- Site: https://maxiaworld.app

Looking forward,
— Alexis
