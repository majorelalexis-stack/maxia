# Prompt pour Claude Chrome — Réparation permissions bot Discord MAXIA

**Usage**: copie tout ce qui est entre les lignes `===` ci-dessous et colle dans Claude Chrome (l'extension navigateur). Claude Chrome va ouvrir Discord dans l'onglet actif, naviguer, et te guider pas à pas.

---

===PROMPT START===

Tu es sur un navigateur Chrome. Je veux que tu m'aides à **réparer les permissions d'un bot Discord** qui échoue systématiquement avec l'erreur suivante depuis ~6h :

```
POST https://discord.com/api/v10/channels/1491798682239111178/messages
→ 403 Forbidden
{"message": "Missing Access", "code": 50001}
```

**Contexte** :
- Le bot s'appelle **MAXIA CEO** (ou similaire, à confirmer visuellement).
- Il est censé poster un daily update dans le channel `#community-news` du serveur Discord **MAXIA Community** toutes les ~3 minutes.
- L'erreur `50001 Missing Access` signifie soit (a) le bot n'est pas invité dans le serveur, soit (b) il est invité mais n'a pas la permission `View Channel` ou `Send Messages` sur ce channel spécifique, soit (c) le channel ID `1491798682239111178` est incorrect ou a été supprimé.

**Ce que je veux que tu fasses, étape par étape** :

### Étape A — Identifier le bot
1. Ouvre https://discord.com/developers/applications dans l'onglet actif.
2. Liste-moi toutes les applications que tu vois (nom + Application ID).
3. Cherche celle qui s'appelle MAXIA CEO, MAXIA Bot, MAXIA Outreach, ou similaire.
4. Si tu en trouves plusieurs qui ressemblent, affiche-les toutes et demande-moi laquelle est la bonne.
5. Note l'Application ID du bot identifié — tu en auras besoin à l'étape D.

### Étape B — Vérifier que le bot est dans le serveur MAXIA Community
1. Ouvre https://discord.com/channels/@me dans l'onglet actif (Discord web).
2. Si je ne suis pas connecté, dis-le moi et arrête-toi là.
3. Dans la liste des serveurs (colonne gauche), cherche un serveur nommé **MAXIA Community** (ou MAXIA tout court, ou MAXIA AI Marketplace).
4. Clique dessus.
5. Clique sur le nom du serveur en haut à gauche → **Server Settings** → **Members**.
6. Dans la barre de recherche des membres, tape "MAXIA" puis "bot".
7. Liste-moi tous les bots que tu vois dans ce serveur.
8. **Si le bot MAXIA n'est PAS listé** → c'est la cause du 403. Passe à l'étape D (générer URL d'invitation).
9. **Si le bot EST listé** → passe à l'étape C.

### Étape C — Vérifier les permissions sur le channel #community-news
1. Dans la liste des channels du serveur, cherche `#community-news` (ou similaire — peut aussi s'appeler `#announcements`, `#general`, `#daily-update`).
2. Pour chaque channel candidat, survole-le → icône roue dentée → **Edit Channel** → onglet **Permissions**.
3. Dans la section **Roles/Members**, cherche soit :
   - Le nom du bot directement (avatar + nom)
   - Un rôle appelé `Bots`, `MAXIA Bot`, `Integration`, ou `@bot`
4. Si tu trouves une entrée → clique dessus → vérifie les 4 permissions suivantes :
   - ✅ **View Channel** (doit être coché vert ou héritage vert)
   - ✅ **Send Messages** (vert)
   - ✅ **Embed Links** (vert)
   - ✅ **Read Message History** (vert)
5. **Si l'une est rouge (❌) ou grise (héritage bloqué)** → c'est la cause. Coche-la en vert explicite, clique **Save Changes** en bas de page.
6. **Si toutes sont vertes** → le bug n'est pas ici. Possible que le channel ID `1491798682239111178` pointe vers un autre channel du même serveur ou d'un autre serveur. Passe à l'étape E.
7. Pendant que tu regardes l'onglet Edit Channel, note aussi l'**ID du channel** : click droit sur le channel dans la colonne gauche → **Copy Channel ID** (il faut avoir activé Developer Mode dans User Settings → Advanced → Developer Mode, fais-le si nécessaire).
8. Compare l'ID copié avec `1491798682239111178`. S'ils diffèrent, c'est la cause : la config du CEO pointe vers un mauvais channel.

### Étape D — Générer une URL d'invitation si le bot est absent
1. Retourne sur https://discord.com/developers/applications
2. Clique sur le bot identifié à l'étape A.
3. Menu gauche → **OAuth2** → **URL Generator** (ou **OAuth2 URL Generator** selon la version Discord).
4. Dans **Scopes** (en haut), coche **`bot`**.
5. En bas apparaît **Bot Permissions**. Coche au minimum :
   - ✅ View Channels
   - ✅ Send Messages
   - ✅ Embed Links
   - ✅ Attach Files
   - ✅ Read Message History
   - ✅ Use External Emojis
6. Copie l'**URL générée** tout en bas de la page.
7. Ouvre cette URL dans l'onglet actif.
8. Sélectionne **MAXIA Community** dans le dropdown.
9. Clique **Authorize** → complète le captcha si demandé.
10. Retourne dans le serveur Discord → **Server Settings** → **Members** → vérifie que le bot est maintenant listé.
11. Refais l'étape C pour confirmer les permissions channel.

### Étape E — Trouver le bon channel si l'ID ne correspond pas
1. Dans Discord web, assure-toi que Developer Mode est activé (User Settings → Advanced → Developer Mode ON).
2. Click droit sur chaque channel du serveur MAXIA Community → **Copy Channel ID** → compare avec `1491798682239111178`.
3. Si aucun ne matche → le channel a été supprimé OU appartient à un autre serveur. Dis-le moi.
4. Si un autre channel matche → dis-moi son nom exact (ex: `#announcements`) — je devrai changer la référence dans la config du CEO.
5. Si le channel `#community-news` existe mais avec un ID différent → dis-moi le nouvel ID, je mettrai à jour `local_ceo/memory_prod/outreach_channels.json`.

### Ce que je veux en sortie
Après chaque étape, dis-moi :
- Ce que tu as trouvé (oui/non, quels noms, quels IDs)
- Ce que tu as fait (cliqué, changé, sauvegardé)
- Si tu es bloqué et pourquoi (pas connecté, élément introuvable, captcha, etc.)

N'avance pas à l'étape suivante tant que tu n'as pas confirmé la précédente.

**IMPORTANT** : Ne modifie AUCUN autre paramètre Discord que ceux listés ci-dessus. Si tu vois quelque chose d'inattendu (autre bot, autre channel qui semble louche, messages privés), signale-le mais ne touche à rien.

Commence par l'étape A.

===PROMPT END===

---

## Ce que tu fais après que Claude Chrome ait fini

1. **Si le bot a été invité OU les permissions corrigées** : rien à faire côté code. Le CEO local va automatiquement réussir `community_news` au prochain cycle (~3 min après le fix).

2. **Si l'ID du channel doit changer** : ouvre `local_ceo/memory_prod/outreach_channels.json`, cherche la clé `discord_ceo.community_server.channels.announcements`, remplace l'ancien ID par le nouveau, sauvegarde. Relance le CEO via `start_ceo.bat`.

3. **Si Claude Chrome est bloqué à l'étape A ou B** (pas connecté Discord, pas connecté Developer Portal) : connecte-toi manuellement dans Chrome sur https://discord.com et https://discord.com/developers, puis relance le prompt.

4. **Si le bot MAXIA CEO n'existe pas du tout dans le Developer Portal** : il faut en créer un nouveau via https://discord.com/developers/applications → **New Application**, puis me redonner le token pour mettre à jour `local_ceo/.env`.

## Infos à avoir sous la main avant de lancer

- Compte Discord connecté dans Chrome (en tant qu'admin du serveur MAXIA Community)
- Compte Discord Developer Portal connecté dans Chrome
- Developer Mode activé dans Discord (User Settings → Advanced)
- Le channel ID à comparer : **`1491798682239111178`**
