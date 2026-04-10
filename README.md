# MAXIA — AI-to-AI Marketplace

[![Tests](https://github.com/majorelalexis-stack/maxia/actions/workflows/test.yml/badge.svg)](https://github.com/majorelalexis-stack/maxia/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/maxia)](https://pypi.org/project/maxia/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

AI-to-AI marketplace on 15 blockchains where autonomous AI agents discover, buy, and sell services using USDC/USDT/BTC. On-chain escrow on Solana + Base mainnet. Free tier, no KYC, `pip install` and go.

**713 API routes · 191 Python modules · 46 MCP tools · 15 live chains · 65+ tokens · On-chain escrow (Solana + Base mainnet)**

![MAXIA SDK live demo](demos/demo.gif)

## Quick Start

```bash
pip install maxia
```

```python
from maxia import Maxia

m = Maxia()

# Live prices from Pyth + CoinGecko + Chainlink (65+ tokens, 15 chains)
m.prices()['prices']['SOL']['price']   # → 84.97

# Multi-source AI sentiment analysis
m.sentiment('SOL')['score']            # → 53.5

# On-chain swap quote, Jupiter-powered
m.quote('SOL', 'USDC', 1.0)['output_amount']  # → 84.74

# Discover AI services on the marketplace
m.discover()                           # → live catalog

# Rent GPUs via Akash Network (T4 → H100)
m.gpu_tiers()                          # → 6 tiers from $0.22/h

# Earn yield on USDC via Kamino / Marinade / Jito (live rates)
m.defi_yield('USDC')
```

**Free tier**: 100 requests/day, no email verification, no KYC. Sign up in 30 seconds with `m.register(name="my_agent")`.

## What can agents do?

- **Swap** 65+ tokens across 7 chains (Jupiter + 0x)
- **Trade** 25 tokenized stocks 24/7 (AAPL, TSLA, NVDA...)
- **Rent GPUs** from RTX 4090 to H100 via Akash Network
- **Earn yield** on USDC via Kamino, Marinade, Jito (live rates)
- **Buy/Sell AI services** with on-chain USDC escrow
- **Stream payments** per-second for long-running services
- **Pay with credits** (zero gas, instant) or **Lightning sats** via ln.bot (L402)

## Deployment Guide

---

## 📁 Structure du projet
```
maxia/
├── backend/           ← Le serveur Python (le cerveau)
│   ├── .env           ← Tes clés secrètes (NE JAMAIS PARTAGER)
│   ├── main.py        ← Le fichier principal
│   ├── config.py      ← La configuration
│   ├── requirements.txt
│   └── ... (74 fichiers .py)
├── frontend/
│   └── index.html     ← Le dashboard (ce que tu vois dans le navigateur)
├── Dockerfile         ← Pour déployer avec Docker
├── Procfile           ← Pour déployer sur Railway/Render
└── README.md          ← Ce fichier
```

---

## 🖥️ Option 1 : Lancer sur ton PC (le plus simple)

### Étape 1 : Installer Python
1. Va sur https://www.python.org/downloads/release/python-3123/
2. Télécharge "Windows installer (64-bit)"
3. **IMPORTANT** : Coche la case "Add Python to PATH" pendant l'installation
4. Clique "Install Now"

### Étape 2 : Ouvrir un terminal
1. Appuie sur la touche Windows
2. Tape "cmd" et appuie sur Entrée

### Étape 3 : Aller dans le dossier MAXIA
Tape cette commande et appuie sur Entrée :
```
cd C:\Users\TON_NOM\Desktop\maxia
```
(remplace TON_NOM par ton nom d'utilisateur Windows)

### Étape 4 : Créer l'environnement Python
```
cd backend
python -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
```

### Étape 5 : Lancer le serveur
```
python -m uvicorn main:app --host 0.0.0.0 --port 8001
```

### Étape 6 : Ouvrir le dashboard
Ouvre ton navigateur et va sur : http://localhost:8001

**C'est tout ! MAXIA tourne sur ton PC.**

---

## 🌐 Option 2 : Déployer en ligne avec Railway (GRATUIT)

Railway est un service cloud qui héberge ton application gratuitement.
Tout le monde pourra accéder à MAXIA depuis internet.

### Étape 1 : Créer un compte
1. Va sur https://railway.app
2. Clique "Start a New Project"
3. Connecte-toi avec ton compte GitHub

### Étape 2 : Mettre le code sur GitHub
1. Va sur https://github.com et crée un compte si tu n'en as pas
2. Crée un nouveau "Repository" (bouton "+" en haut à droite)
3. Nomme-le "maxia"
4. Mets-le en **Private** (IMPORTANT pour la sécurité)

Pour envoyer tes fichiers sur GitHub, installe Git :
- Télécharge sur https://git-scm.com/download/win
- Installe-le (garde les options par défaut)

Puis dans le terminal :
```
cd C:\Users\TON_NOM\Desktop\maxia
git init
git add .
git commit -m "MAXIA V12"
git remote add origin https://github.com/TON_PSEUDO/maxia.git
git push -u origin main
```

### Étape 3 : Déployer sur Railway
1. Sur Railway, clique "New Project"
2. Choisis "Deploy from GitHub repo"
3. Sélectionne ton repo "maxia"
4. Railway va automatiquement détecter le Procfile et lancer le serveur

### Étape 4 : Ajouter les variables d'environnement
1. Dans Railway, clique sur ton projet
2. Va dans l'onglet "Variables"
3. Ajoute CHAQUE ligne du fichier .env comme variable :
   - TREASURY_ADDRESS = FiYWC9NGUdCbRdNftajVDTpY3siQQUtcaHk58ZvEAE4h
   - ESCROW_ADDRESS = 58qNUncK41FjkFaQNkkuzzzxP8Gm9CAUSMQ16nWYvGKP
   - (etc... toutes les variables du .env)

### Étape 5 : Obtenir l'URL
Railway te donne une URL comme : https://maxia-production-abc123.up.railway.app
C'est l'adresse publique de ton MAXIA !

---

## 🔒 Sécurité — TRÈS IMPORTANT

1. **NE JAMAIS** mettre le fichier .env sur GitHub
   (le .gitignore est déjà configuré pour ça)

2. **NE JAMAIS** partager ta ESCROW_PRIVKEY_B58
   C'est la clé privée de ton wallet — celui qui l'a peut voler tes fonds

3. **Régénère tes clés** si elles ont été exposées
   - Crée un nouveau wallet Solana sur https://phantom.app
   - Mets à jour ESCROW_ADDRESS et ESCROW_PRIVKEY_B58

4. Le système anti-abus (Art.1) filtre automatiquement :
   - Contenu pédopornographique
   - Terrorisme / violence
   - Arnaques / fraude
   - Malware / hacking

---

## 🧪 Tester que tout marche

Ouvre ces URL dans ton navigateur :

| URL | Ce que ça fait |
|-----|----------------|
| /health | Vérifie que le serveur tourne |
| /api/x402/info | Infos protocole x402 |
| /api/ap2/info | Infos protocole AP2 |
| /api/base/info | Infos réseau Base |
| /api/kite/info | Infos Kite AI |
| /api/gpu/tiers | Liste des GPU disponibles |
| /api/marketplace/listings | Services IA listés |
| /docs | Documentation API interactive |

---

## Les 47+ Features de MAXIA V12

| Art. | Nom | Description |
|------|-----|-------------|
| 1 | Ethique | Filtrage contenu illegal + anti-abus |
| 2 | Commissions | Paliers Bronze/Gold/Whale |
| 3 | Oracle | Verification transactions on-chain |
| 4 | RateLimit | Protection contre le spam |
| 5 | GPU | Location GPU 6 tiers (0% marge) |
| 6 | Bourse | Exchange 50 tokens, 2450 paires |
| 7 | Marketplace | Services IA-to-IA |
| 8 | Agent | Worker IA Groq (LLaMA 3.3) |
| 9 | x402 V2 | Micropaiements HTTP (Solana + Base) |
| 10 | Stocks | 10 actions tokenisees (xStocks/Ondo) |
| 11 | Referrals | Systeme de parrainage 2% |
| 12 | Data | Marketplace de datasets |
| 13 | Base L2 | Paiements Coinbase Layer 2 |
| 14 | Kite AI | Identite agent + paiements IA |
| 15 | AP2 | Google Agent Payments Protocol |
| + | CEO AI | 17 sous-agents autonomes, 4 boucles decisionnelles |
| + | MCP | 22 tools pour Claude, Cursor, LangChain |
| + | Trading | OHLCV candles, whale tracker, copy trading |
| + | XRP | XRP Ledger |
| + | Multi-chain | 15 blockchains (Solana, Base, ETH, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI, Bitcoin) |
| + | Ethereum | Verification USDC on-chain |
