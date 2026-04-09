# Tutoriel Alexis — Discord outreach setup (Plan CEO V7 / Sprint 5)

**Duree totale : ~25 minutes**. A faire une seule fois.

Une fois ce tuto fini, le backend MAXIA enverra automatiquement les messages outreach
et TU fera juste l'animation manuelle dans les serveurs externes ou tu participes.

---

## Contexte

Discord interdit aux bots d'etre ajoutes a un serveur par quelqu'un qui n'est pas
**admin** de ce serveur. Tu n'es pas admin des serveurs crypto externes. Donc :

- **Bot MAXIA outreach** = dans TES propres serveurs uniquement
- **Toi en tant qu'utilisateur** (compte `maxia_alexis`) = dans les serveurs externes
- **Outreach externe** = tu le fais manuellement, le bot te sert d'outil interne

C'est la seule facon legale et sans risque ban de procéder.

---

## Partie 1 — Creer le serveur "MAXIA Community" (5 min)

Ce serveur sera **public** et te permet d'inviter les prospects a venir vers toi
(pas l'inverse). C'est le pattern des projets crypto pro (Solana, Ethereum, etc.).

### Etapes

1. **Ouvre Discord** (app ou web)
2. Colonne gauche → gros bouton `+` en bas → **"Creer mes propres serveurs"**
3. **"Pour un club, une communaute ou autre"**
4. **Nom** : `MAXIA Community`
5. **Icone** : upload ton logo MAXIA (optionnel)
6. **Clic "Creer"**
7. Tu arrives dans le serveur

### Configuration de base

1. **Clic droit sur le nom du serveur** (en haut a gauche) → **"Parametres du serveur"**
2. Onglet **"Apercu"** :
   - Description : `AI-to-AI marketplace on 15 blockchains. 46 MCP tools, GPU rental, 65 tokens swap.`
   - Langue : Francais ou Anglais selon ta preference
3. Onglet **"Activer Communaute"** (tout en bas du menu gauche) :
   - Clic **"Commencer"**
   - Suis les etapes (accepter les regles communautaires Discord, activer le niveau de verification Moyen)
   - Ca te debloque : channels d'annonces, regles, welcome screen, stats, etc.

### Channels recommandes (cree-les dans cet ordre)

Supprime `#general` par defaut et cree cette structure :

**Categorie : INFOS**
- `#annonces` (lecture seule, moderators only)
- `#regles` (lecture seule)
- `#roadmap` (lecture seule)

**Categorie : COMMUNITY**
- `#general` (discussions)
- `#support` (questions)
- `#dev-talk` (devs qui integrent MAXIA SDK)

**Categorie : REGIONS**
- `#asia`
- `#latam`
- `#africa`
- `#europe`
- `#japan`

### Role owner

1. Parametres → **Roles** → **Creer un role**
2. Nom : `Founder`, couleur orange, admin permissions
3. Assigne-toi ce role

### Widget / invite permanent

1. Parametres → **Widget** → active
2. Copie l'**URL d'invitation** (instant-invite)
3. Ca sera ton lien public a coller partout (Twitter bio, site, emails, etc.)

---

## Partie 2 — Ajouter le bot MAXIA outreach a MAXIA Community (1 min)

1. Colle cet URL dans un nouvel onglet browser :
   ```
   https://discord.com/oauth2/authorize?client_id=1491786027143004180&permissions=19456&integration_type=0&scope=bot
   ```
2. Dropdown "Ajouter au serveur" → choisis **MAXIA Community**
3. Clic **Continuer** → **Autoriser** → captcha si demande
4. Retourne sur MAXIA Community → tu dois voir **MAXIA outreach** dans la liste des membres

### Donne un role au bot

1. Parametres du serveur → **Roles** → creer role `Bot`
2. Permissions : `Send Messages`, `Embed Links`, `Read Message History`, `View Channels`
3. Assigne ce role au membre `MAXIA outreach`

---

## Partie 3 — Recuperer les IDs pour le backend (2 min)

Le backend a besoin du **server_id** et du **channel_id** pour envoyer des messages.

### Activer Mode Developpeur (une fois pour toutes)

1. Settings Discord (rouage en bas a gauche) → **Avance** → active **"Mode Developpeur"**
2. Clic OK

### Copier les IDs

1. **Server ID** :
   - Clic droit sur le nom du serveur **MAXIA Community** (en haut gauche)
   - **Copier l'ID du serveur**
2. **Channel IDs** (pour chaque channel important) :
   - Clic droit sur `#annonces` → **Copier l'ID du salon**
   - Clic droit sur `#general` → **Copier l'ID du salon**
   - Meme chose pour `#asia`, `#latam`, etc.

### Colle-moi ces IDs ici

Format :
```
MAXIA Community
  server_id: 1234567890
  #annonces: 2345678901
  #general: 3456789012
  #asia: ...
  #latam: ...
  #africa: ...
  #europe: ...
  #japan: ...
```

Je les sauvegarde dans `local_ceo/memory_prod/outreach_channels.json`.

---

## Partie 4 — Rejoindre les serveurs externes (5 min)

**IMPORTANT** : tu rejoins avec **TON compte perso Alexis** (ou un compte dedie `maxia_alexis` si tu en as cree un). **PAS avec le bot**.

Je te donne 5 serveurs recommandes dans le message suivant (l'agent de recherche
est en train de les trouver). Pour chacun :

1. Clic sur l'invite link → rejoins
2. Lis le channel `#rules` et `#welcome` (obligatoire, certains serveurs bannissent si tu ignores)
3. **NE POSTE RIEN** pendant 72 heures (observation passive = warming humain)
4. React a quelques emojis pour montrer que tu lis (pas spam)

### Warming manuel (14 jours — le "human warming" pas le bot)

**J1-3 : Observation silencieuse**
- 0 message
- Lis les discussions
- Repere les sujets chauds

**J4-7 : Engagement leger**
- 1-2 messages par jour max
- Reponds a des questions tech ou crypto
- Aide d'autres membres
- **AUCUNE mention MAXIA**

**J8-14 : Mention organique**
- Quand qqn pose une question ou MAXIA est pertinent → "I built something for this, check X"
- Maximum 1 mention par jour
- Jamais de lien direct dans les 5 premiers messages
- Link en bio uniquement

**J15+ : Full operation**
- 5 messages max par jour par serveur
- Toujours value-first
- Reponds toujours aux DMs qui te contactent

---

## Partie 5 — Message Content Intent (optionnel, plus tard)

**Pas besoin pour demarrer.** Cette intent sert seulement si tu veux que le bot
**LISE** les messages dans les channels. Pour l'outreach qu'on construit (envoi
de messages), ce n'est pas necessaire.

### Quand tu en auras besoin (plus tard)

- Pour que le bot reponde automatiquement aux mentions (`@MAXIA outreach price BTC`)
- Pour qu'il detecte des mots-cles et reponde
- Pour moderer automatiquement

### Comment l'activer (quand tu en auras besoin)

1. https://discord.com/developers/applications
2. Clic sur **MAXIA outreach**
3. Menu gauche → **Bot**
4. Scroll jusqu'a **Privileged Gateway Intents**
5. Toggle **Message Content Intent** sur ON
6. **Enregistrer les modifications** (bouton vert en bas)

**Note Discord** : au-dessus de 100 serveurs, il faut que ton bot soit
"verifie" par Discord pour garder cette intent. Pour l'instant tu es a 1
serveur, pas d'inquietude.

---

## Partie 6 — Deployer sur le VPS (optionnel)

Le backend est pret. Pour que ca tourne en prod :

```bash
cd /root/maxia
git pull origin main
systemctl restart maxia-backend
curl https://maxiaworld.app/api/ceo/gateway/status
```

Puis depuis ton PC local, test :
```bash
python scripts/test_discord_bot.py
```

Le message doit apparaitre dans `#general` de MAXIA Community (ou le channel test).

---

## Recap actions TOI

- [ ] **Creer** le serveur `MAXIA Community`
- [ ] **Activer** mode Communaute + structurer channels
- [ ] **Ajouter** le bot via OAuth2 URL
- [ ] **Activer** Mode Developpeur Discord
- [ ] **Copier** server_id + channel_ids → me les donner
- [ ] **Rejoindre** les 5 serveurs externes (avec TON compte, pas le bot)
- [ ] **Observer** 72h puis warming progressif

## Recap actions CLAUDE (ce que je fais)

- [x] Backend `DiscordOutreach` avec vrai API Discord
- [x] Smoke test live verifie (message livre)
- [x] Token dans `.env`
- [x] Plan CEO V7 memorise
- [x] **Recherche 5 serveurs** (en cours via agent)
- [ ] Resolution invite codes -> guild/channel IDs via API Discord publique
- [ ] Sauvegarde dans `local_ceo/memory_prod/outreach_channels.json`
- [ ] Preparer script warming auto (apres que tu me donnes MAXIA Community IDs)

---

## Questions pour Alexis apres ce tuto

1. Tu as cree MAXIA Community ? Colle server_id + les 3-5 channel IDs principaux
2. Tu es pret a rejoindre les 5 serveurs externes manuellement ?
3. Tu veux que je code le script warming automatique pour MAXIA Community (le bot poste des annonces une fois par jour) ?
4. Tu veux un bot welcome message auto quand qqn rejoint MAXIA Community ?

Reponds une fois le serveur cree et les IDs copies.
