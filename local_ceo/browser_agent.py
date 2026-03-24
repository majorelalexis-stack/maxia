"""Browser Agent — Playwright ameliore avec multi-selectors, role-based, retry.

Zero LLM, zero token. Robuste grace a :
- Multi-selectors avec fallback (3-4 alternatives par element)
- Selection par role/texte (pas juste data-testid)
- Attente intelligente (wait_for_selector au lieu de sleep)
- Screenshot avant/apres pour debug
- Auto-detection login
- Retry avec backoff
"""
import asyncio
import os
import time
from config_local import BROWSER_PROFILE_DIR, MAX_TWEETS_DAY, MAX_REDDIT_POSTS_DAY


# Rate limits pour eviter les bans (actions par minute)
_RATE_LIMITS = {
    "tweet": {"per_min": 2, "per_day": MAX_TWEETS_DAY},
    "like": {"per_min": 5, "per_day": 50},
    "follow": {"per_min": 2, "per_day": 10},
    "reply": {"per_min": 2, "per_day": 20},
    "reddit_post": {"per_min": 1, "per_day": MAX_REDDIT_POSTS_DAY},
    "reddit_comment": {"per_min": 1, "per_day": 15},
    "reddit_upvote": {"per_min": 2, "per_day": 20},
    "dm": {"per_min": 1, "per_day": 10},
}


class BrowserAgent:
    """Controle un navigateur Chrome via Playwright avec selectors robustes."""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False
        self._daily_counts = {"date": ""}
        self._minute_counts = {}  # {action: [timestamps]}
        self._action_history = []  # Deduplication: [{action, hash, ts}]
        self._profile_dir = BROWSER_PROFILE_DIR
        self._dedup_file = os.path.join(os.path.dirname(__file__), ".browser_dedup.json")
        self._load_dedup()

    def _load_dedup(self):
        """Charge l'historique de dedup depuis le disque (survit aux restarts)."""
        try:
            if os.path.exists(self._dedup_file):
                import json
                with open(self._dedup_file, "r") as f:
                    data = json.load(f)
                cutoff = time.time() - 86400 * 3  # Garder 3 jours
                self._action_history = [a for a in data if a.get("ts", 0) > cutoff]
                print(f"[BrowserAgent] Dedup loaded: {len(self._action_history)} actions")
        except Exception:
            self._action_history = []

    def _save_dedup(self):
        """Sauvegarde l'historique de dedup sur disque."""
        try:
            import json
            with open(self._dedup_file, "w") as f:
                json.dump(self._action_history[-500:], f)
        except Exception:
            pass

    def _reset_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if self._daily_counts.get("date") != today:
            self._daily_counts = {"date": today}
            self._action_history = [a for a in self._action_history if a["ts"] > time.time() - 86400]

    def _check_rate(self, action_type: str) -> str | None:
        """Verifie les rate limits. Retourne None si OK, sinon le message d'erreur."""
        self._reset_if_new_day()
        limits = _RATE_LIMITS.get(action_type, {"per_min": 3, "per_day": 30})

        # Limite par jour
        day_count = self._daily_counts.get(action_type, 0)
        if day_count >= limits["per_day"]:
            return f"Limite {action_type}/jour atteinte ({limits['per_day']})"

        # Limite par minute
        now = time.time()
        timestamps = self._minute_counts.get(action_type, [])
        timestamps = [t for t in timestamps if t > now - 60]
        self._minute_counts[action_type] = timestamps
        if len(timestamps) >= limits["per_min"]:
            return f"Rate limit {action_type}: {limits['per_min']}/min"

        return None

    def _record_action(self, action_type: str, content_hash: str = ""):
        """Enregistre une action pour rate limiting et deduplication."""
        self._daily_counts[action_type] = self._daily_counts.get(action_type, 0) + 1
        self._minute_counts.setdefault(action_type, []).append(time.time())
        if content_hash:
            self._action_history.append({"action": action_type, "hash": content_hash, "ts": time.time()})
        # Cap action history to prevent memory leak
        if len(self._action_history) > 500:
            self._action_history = self._action_history[-500:]
        # Persister sur disque (survit aux restarts)
        self._save_dedup()
        # Clean old timestamps from minute counts
        now = time.time()
        for key in list(self._minute_counts.keys()):
            self._minute_counts[key] = [t for t in self._minute_counts[key] if t > now - 120]
            if not self._minute_counts[key]:
                del self._minute_counts[key]

    def _is_duplicate(self, action_type: str, content: str) -> bool:
        """Verifie si cette action a deja ete faite (meme contenu)."""
        import hashlib
        h = hashlib.md5(f"{action_type}:{content}".encode()).hexdigest()[:12]
        return any(a["hash"] == h for a in self._action_history)

    def _content_hash(self, action_type: str, content: str) -> str:
        import hashlib
        return hashlib.md5(f"{action_type}:{content}".encode()).hexdigest()[:12]

    def _clean_session_restore_files(self):
        """Supprime les fichiers de session Chrome ET force les prefs a 'page vierge'.
        Empeche Chrome de restaurer les onglets de la session precedente
        (fix: Chrome ouvrait toujours maxiaworld.app au demarrage).
        """
        import shutil, glob, json

        # ETAPE A: Forcer les prefs Chrome a NE PAS restaurer de session
        # restore_on_startup: 1 = new tab page, 4 = continue, 5 = specific URLs
        for prefs_path in [
            os.path.join(self._profile_dir, "Default", "Preferences"),
            os.path.join(self._profile_dir, "Preferences"),
        ]:
            try:
                if os.path.exists(prefs_path):
                    with open(prefs_path, "r", encoding="utf-8") as f:
                        prefs = json.load(f)
                    changed = False
                    # Force "Open the New Tab page" instead of restoring session
                    if prefs.get("browser", {}).get("restore_on_startup") != 1:
                        prefs.setdefault("browser", {})["restore_on_startup"] = 1
                        changed = True
                    # Clear any startup URLs
                    if prefs.get("browser", {}).get("startup_urls"):
                        prefs["browser"]["startup_urls"] = []
                        changed = True
                    # Disable session restore flag
                    if prefs.get("session", {}).get("restore_on_startup") != 1:
                        prefs.setdefault("session", {})["restore_on_startup"] = 1
                        changed = True
                    # Disable crash recovery bubble ("Restaurer les pages?")
                    prefs.setdefault("profile", {})["exit_type"] = "Normal"
                    prefs["profile"]["exited_cleanly"] = True
                    changed = True
                    if changed:
                        with open(prefs_path, "w", encoding="utf-8") as f:
                            json.dump(prefs, f, separators=(",", ":"))
                        print(f"[BrowserAgent] Prefs patched: restore_on_startup=1 (new tab)")
            except Exception as e:
                print(f"[BrowserAgent] Prefs patch warning: {e}")

        # ETAPE B: Supprimer les fichiers de session
        session_paths = [
            os.path.join(self._profile_dir, "Default", "Sessions"),
            os.path.join(self._profile_dir, "Default", "Session Storage"),
            os.path.join(self._profile_dir, "Default", "Current Session"),
            os.path.join(self._profile_dir, "Default", "Current Tabs"),
            os.path.join(self._profile_dir, "Default", "Last Session"),
            os.path.join(self._profile_dir, "Default", "Last Tabs"),
            # Profil sans sous-dossier Default (Playwright persistent context)
            os.path.join(self._profile_dir, "Sessions"),
            os.path.join(self._profile_dir, "Session Storage"),
            os.path.join(self._profile_dir, "Current Session"),
            os.path.join(self._profile_dir, "Current Tabs"),
            os.path.join(self._profile_dir, "Last Session"),
            os.path.join(self._profile_dir, "Last Tabs"),
        ]
        cleaned = 0
        for p in session_paths:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                    cleaned += 1
                elif os.path.isfile(p):
                    os.remove(p)
                    cleaned += 1
            except Exception:
                pass
        # Aussi nettoyer les fichiers Singleton lock
        for lock in glob.glob(os.path.join(self._profile_dir, "Singleton*")):
            try:
                os.remove(lock)
            except Exception:
                pass
        if cleaned:
            print(f"[BrowserAgent] Session restore files cleaned: {cleaned}")

    async def setup(self):
        """Lance Chromium avec profil persistant (cookies gardes, session PAS restauree)."""
        if self._initialized:
            return
        try:
            from playwright.async_api import async_playwright

            # ETAPE 1: Nettoyer les fichiers de session AVANT le lancement
            # Ca garde les cookies (login X, Reddit, etc.) mais empeche la restauration d'onglets
            self._clean_session_restore_files()

            self._pw = await async_playwright().start()
            os.makedirs(self._profile_dir, exist_ok=True)
            # Utiliser le Chrome systeme (pas le Chromium Playwright) pour garder le profil
            import platform
            if platform.system() == "Windows":
                chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            elif platform.system() == "Darwin":
                chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            else:
                chrome_path = "/usr/bin/google-chrome"
            if not os.path.exists(chrome_path):
                chrome_path = None  # Fallback Chromium Playwright
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=self._profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 900},
                executable_path=chrome_path,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--no-restore-session-state",
                    "--disable-session-crashed-bubble",
                    "--homepage=about:blank",
                    "--no-first-run",
                    "--disable-features=SessionRestore",
                ],
                ignore_default_args=["--enable-automation"],
            )
            # ETAPE 2: Reutiliser la premiere page, fermer les autres
            pages = self._context.pages
            if pages:
                self._page = pages[0]
                try:
                    await self._page.goto("about:blank", timeout=5000)
                except Exception:
                    pass
                # Fermer toutes les pages sauf la premiere
                for p in pages[1:]:
                    try:
                        if not p.is_closed():
                            await p.close()
                    except Exception:
                        pass
            else:
                self._page = await self._context.new_page()
            self._initialized = True
            print("[BrowserAgent] Chrome lance — page vierge (session restore desactivee)")
        except Exception as e:
            print(f"[BrowserAgent] Setup failed: {str(e)[:200]}")
            raise

    async def close(self):
        """Ferme le navigateur."""
        if self._context:
            await self._context.close()
        if hasattr(self, '_pw') and self._pw:
            await self._pw.stop()
        self._initialized = False

    async def _cleanup_tabs(self):
        """Ferme tous les onglets sauf self._page. Empeche l'accumulation d'about:blank."""
        if not self._context:
            return
        try:
            pages = self._context.pages
            for p in pages:
                if p != self._page and not p.is_closed():
                    try:
                        await p.close()
                    except Exception:
                        pass
        except Exception:
            pass

    async def _ensure_ready(self):
        if not self._initialized:
            await self.setup()
            return
        # Nettoyer les onglets accumules
        await self._cleanup_tabs()
        # Verifier que le browser est toujours vivant
        try:
            if self._page is None or self._page.is_closed():
                raise Exception("Page closed")
            await self._page.evaluate("1+1")
        except Exception:
            print("[BrowserAgent] Browser mort, reconnexion...")
            self._initialized = False
            # Force cleanup everything — context and playwright may be corrupted
            for obj in [self._context, self._pw]:
                try:
                    if obj is None:
                        continue
                    if hasattr(obj, 'close'):
                        await obj.close()
                    elif hasattr(obj, 'stop'):
                        await obj.stop()
                except Exception:
                    pass
            self._context = None
            self._page = None
            self._pw = None
            # Kill orphan Chrome processes that may hold the profile lock
            import subprocess
            try:
                subprocess.run(
                    ['taskkill', '/IM', 'chrome.exe', '/F'],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
            # Clean lock + session files that prevent relaunch / cause session restore
            self._clean_session_restore_files()
            await asyncio.sleep(3)
            # Retry setup with a fresh playwright instance
            try:
                await self.setup()
            except Exception as e:
                print(f"[BrowserAgent] Reconnexion echouee: {e}")

    async def _handle_dm_verification(self, page):
        """Detecte et saisit le code de verification X pour acceder aux DMs (code: 0085)."""
        try:
            # Chercher un champ de saisie de code verification
            code_input = page.locator('input[type="text"][name*="code" i], input[type="text"][name*="verify" i], input[type="text"][placeholder*="code" i], input[type="tel"], input[data-testid*="ocfEnterTextTextInput"]').first
            if await code_input.is_visible(timeout=2000):
                print("[BrowserAgent] DM verification code detected, entering 0085...")
                await code_input.click()
                await code_input.fill("0085")
                await page.wait_for_timeout(500)
                # Cliquer Next/Submit/Verify
                for sel in ['button:has-text("Next")', 'button:has-text("Suivant")', 'button:has-text("Verify")', 'button:has-text("Submit")', 'button[type="submit"]', '[data-testid="ocfEnterTextNextButton"]']:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        await page.wait_for_timeout(3000)
                        print("[BrowserAgent] DM verification code submitted")
                        return True
        except Exception:
            pass
        return False

    async def _goto_dms(self, page):
        """Navigue vers les DMs et gere la verification si necessaire."""
        await page.goto("https://x.com/messages", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)
        # Verifier si X demande un code
        await self._handle_dm_verification(page)
        await page.wait_for_timeout(2000)

    async def _click_dm_conversation(self, page, index: int = 0) -> bool:
        """Clique sur une conversation DM (gere la nouvelle UI X 2026).
        Utilise mouse.click avec coordonnees exactes car les clicks Playwright
        ne declenchent pas la navigation SPA de X sur les DMs."""
        items = await page.locator('[data-testid^="dm-conversation-item-"]').all()
        if not items or index >= len(items):
            return False
        try:
            # Methode 1: mouse.click au centre exact (simule un vrai clic humain)
            box = await items[index].bounding_box()
            if box:
                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.wait_for_timeout(3000)
                if await page.locator('[data-testid="dm-conversation-panel"]').first.is_visible(timeout=2000):
                    return True
            # Methode 2: double-click
            if box:
                await page.mouse.dblclick(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.wait_for_timeout(3000)
                if await page.locator('[data-testid="dm-conversation-panel"]').first.is_visible(timeout=2000):
                    return True
            # Methode 3: keyboard navigation (Tab + Enter)
            await items[index].focus()
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(3000)
            if await page.locator('[data-testid="dm-conversation-panel"]').first.is_visible(timeout=2000):
                return True
        except Exception:
            pass
        return False

    async def _new_page(self):
        """Cree un nouvel onglet pour les actions paralleles."""
        if not self._initialized:
            await self.setup()
        return await self._context.new_page()

    async def parallel_actions(self, actions: list) -> list:
        """Execute plusieurs actions browser en parallele (multi-onglets).
        actions: [{"method": "like_tweet", "params": {"tweet_url": "..."}}, ...]
        """
        import asyncio as _aio
        results = []

        async def _run_one(action):
            method = action.get("method", "")
            params = action.get("params", {})
            fn = getattr(self, method, None)
            if not fn:
                return {"method": method, "success": False, "error": "Unknown method"}
            try:
                if method in ("like_tweet", "follow_user"):
                    r = await fn(list(params.values())[0] if params else "")
                elif method == "post_tweet":
                    r = await fn(params.get("text", ""))
                elif method == "screenshot_page":
                    r = {"path": await fn(params.get("url", ""))}
                else:
                    r = {"skipped": True}
                return {"method": method, **r}
            except Exception as e:
                return {"method": method, "success": False, "error": str(e)}

        # Executer max 3 en parallele
        for i in range(0, len(actions), 3):
            batch = actions[i:i+3]
            batch_results = await _aio.gather(*[_run_one(a) for a in batch], return_exceptions=True)
            for r in batch_results:
                if isinstance(r, Exception):
                    results.append({"success": False, "error": str(r)})
                else:
                    results.append(r)

        return results

    # ── Helpers robustes ──

    async def _find_and_click(self, page, selectors: list, description: str, timeout: int = 10000) -> bool:
        """Essaie plusieurs selectors jusqu'a en trouver un qui marche."""
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=timeout):
                    await el.click()
                    return True
            except Exception:
                continue
        print(f"[BrowserAgent] {description}: aucun selector trouve parmi {len(selectors)}")
        return False

    async def _find_and_fill(self, page, selectors: list, text: str, description: str, timeout: int = 10000) -> bool:
        """Essaie plusieurs selectors pour remplir un champ."""
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=timeout):
                    await el.click()
                    try:
                        await el.fill(text)
                        return True
                    except Exception:
                        # fill() echoue sur contenteditable — fallback keyboard.type()
                        await page.keyboard.type(text, delay=15)
                        return True
            except Exception:
                continue
        print(f"[BrowserAgent] {description}: aucun selector trouve")
        return False

    async def _screenshot(self, page, name: str) -> str:
        """Screenshot de preuve."""
        path = os.path.join(self._profile_dir, f"{name}_{int(time.time())}.png")
        try:
            await page.screenshot(path=path)
            return path
        except Exception:
            return ""

    async def _is_logged_in_twitter(self, page) -> bool:
        """Verifie si on est connecte sur X."""
        try:
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            # Si redirige vers login, on n'est pas connecte
            url = page.url
            if "login" in url or "i/flow" in url:
                return False
            # Chercher le bouton compose
            compose = page.locator('[data-testid="SideNav_NewTweet_Button"], a[href="/compose/post"], [aria-label*="Post"], [aria-label*="Poster"]')
            return await compose.first.is_visible(timeout=3000)
        except Exception:
            return False

    async def _is_logged_in_reddit(self, page) -> bool:
        """Verifie si on est connecte sur Reddit."""
        try:
            await page.goto("https://www.reddit.com", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            # Chercher le bouton user ou create post
            user = page.locator('button[id*="USER"], [data-testid="create-post"], a[href*="/submit"]')
            return await user.first.is_visible(timeout=3000)
        except Exception:
            return False

    # ── Actions principales ──

    async def post_tweet(self, text: str, media: str = None) -> dict:
        """Poste un tweet sur X avec multi-selectors robustes."""
        err = self._check_rate("tweet")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("tweet", text):
            return {"success": False, "error": "Tweet deja poste (doublon)"}

        await self._ensure_ready()
        page = self._page

        # Verifier login
        if not await self._is_logged_in_twitter(page):
            return {"success": False, "error": "Non connecte sur X. Ouvre Chrome avec le profil et connecte-toi."}

        try:
            # Naviguer vers compose
            await page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Remplir le texte — multi-selectors
            filled = await self._find_and_fill(page, [
                '[data-testid="tweetTextarea_0"]',
                '[data-testid="tweetTextarea_0_label"]',
                'div[role="textbox"][contenteditable="true"]',
                '.public-DraftEditor-content',
                '[aria-label*="post" i] div[contenteditable]',
                '[aria-label*="tweet" i] div[contenteditable]',
            ], text[:280], "Tweet textbox")

            if not filled:
                await self._screenshot(page, "tweet_fill_fail")
                return {"success": False, "error": "Impossible de remplir le champ tweet"}

            await page.wait_for_timeout(1000)

            # Upload media
            if media and os.path.exists(media):
                try:
                    file_input = page.locator('input[type="file"][accept*="image"]').first
                    await file_input.set_input_files(media)
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"[BrowserAgent] Media upload failed: {e}")

            # Cliquer Post — multi-selectors
            posted = await self._find_and_click(page, [
                '[data-testid="tweetButton"]',
                '[data-testid="tweetButtonInline"]',
                'button[role="button"]:has-text("Post")',
                'button[role="button"]:has-text("Poster")',
                'div[role="button"]:has-text("Post")',
                'div[role="button"]:has-text("Poster")',
            ], "Post button")

            if not posted:
                await self._screenshot(page, "tweet_post_fail")
                return {"success": False, "error": "Bouton Post introuvable"}

            await page.wait_for_timeout(5000)
            proof = await self._screenshot(page, "tweet_ok")

            self._record_action("tweet", self._content_hash("tweet", text))
            return {"success": True, "proof": proof, "text": text[:100]}

        except Exception as e:
            await self._screenshot(page, "tweet_error")
            return {"success": False, "error": str(e)}

    async def reply_tweet(self, tweet_url: str, text: str) -> dict:
        """Repond a un tweet specifique."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Cliquer sur le champ reponse
            filled = await self._find_and_fill(page, [
                '[data-testid="tweetTextarea_0"]',
                'div[role="textbox"][contenteditable="true"]',
                '[aria-label*="reply" i] div[contenteditable]',
                '[aria-label*="Post your reply" i]',
            ], text[:280], "Reply textbox")

            if not filled:
                return {"success": False, "error": "Champ reponse introuvable"}

            await page.wait_for_timeout(1000)

            posted = await self._find_and_click(page, [
                '[data-testid="tweetButton"]',
                '[data-testid="tweetButtonInline"]',
                'button:has-text("Reply")',
                'button:has-text("Repondre")',
            ], "Reply button")

            if not posted:
                return {"success": False, "error": "Bouton Reply introuvable"}

            await page.wait_for_timeout(5000)
            return {"success": True, "url": tweet_url, "reply": text[:100]}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete_recent_replies(self, max_delete: int = 20) -> dict:
        """Supprime les replies/commentaires recents du compte MAXIA."""
        await self._ensure_ready()
        page = self._page

        try:
            # Aller sur le profil "Replies" tab
            await page.goto("https://x.com/MAXIA_WORLD/with_replies", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            deleted = 0
            for _ in range(max_delete):
                try:
                    # Trouver un tweet avec le menu "..."
                    articles = await page.locator('article[data-testid="tweet"]').all()
                    if not articles:
                        break

                    found = False
                    for article in articles[:5]:
                        # Verifier si c'est notre reply (pas un retweet)
                        try:
                            article_text = await article.inner_text()
                            # Skip si c'est un tweet original (pas un reply)
                            if "Replying to" not in article_text and "En réponse à" not in article_text:
                                continue
                        except Exception:
                            continue

                        # Cliquer le menu "..."
                        menu_btn = article.locator('[data-testid="caret"]').first
                        if await menu_btn.is_visible(timeout=2000):
                            await menu_btn.click()
                            await page.wait_for_timeout(1000)

                            # Cliquer "Delete" / "Supprimer"
                            delete_btn = page.locator('[data-testid="Dropdown"] [role="menuitem"]:has-text("Delete"), [data-testid="Dropdown"] [role="menuitem"]:has-text("Supprimer")').first
                            if await delete_btn.is_visible(timeout=2000):
                                await delete_btn.click()
                                await page.wait_for_timeout(1000)

                                # Confirmer
                                confirm_btn = page.locator('[data-testid="confirmationSheetConfirm"], button:has-text("Delete"), button:has-text("Supprimer")').first
                                if await confirm_btn.is_visible(timeout=2000):
                                    await confirm_btn.click()
                                    await page.wait_for_timeout(2000)
                                    deleted += 1
                                    found = True
                                    print(f"[BrowserAgent] Deleted reply #{deleted}")
                            else:
                                # Fermer le menu si pas de Delete
                                await page.keyboard.press("Escape")
                                await page.wait_for_timeout(500)
                    if not found:
                        break
                except Exception:
                    break

            return {"success": True, "deleted": deleted}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete_all_dms(self, max_delete: int = 50) -> dict:
        """Supprime toutes les conversations DM via clic droit -> Delete conversation."""
        await self._ensure_ready()
        page = self._page

        try:
            await self._goto_dms(page)

            deleted = 0
            for _ in range(max_delete):
                items = await page.locator('[data-testid^="dm-conversation-item-"]').all()
                if not items:
                    break

                # Clic droit sur la conversation (mouse.click button right)
                box = await items[0].bounding_box()
                if not box:
                    break
                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2, button='right')
                await page.wait_for_timeout(1500)

                # Chercher Delete conversation dans le menu contextuel
                del_btn = page.locator('text=Delete conversation').first
                if not await del_btn.is_visible(timeout=2000):
                    del_btn = page.locator('[role="menuitem"]:has-text("Delete")').first
                if await del_btn.is_visible(timeout=1000):
                    await del_btn.click()
                    await page.wait_for_timeout(1000)
                    confirm = page.locator('[data-testid="confirmationSheetConfirm"]').first
                    if await confirm.is_visible(timeout=3000):
                        await confirm.click()
                        await page.wait_for_timeout(2000)
                        deleted += 1
                        print(f"[BrowserAgent] Deleted DM #{deleted}")
                        continue

                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)

                if deleted == 0 and _ > 3:
                    break

            return {"success": True, "deleted": deleted}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def quote_tweet(self, tweet_url: str, text: str) -> dict:
        """Quote tweet une URL avec un commentaire."""
        err = self._check_rate("tweet")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("tweet", tweet_url):
            return {"success": False, "error": "Quote tweet deja fait pour cette URL (doublon)"}

        await self._ensure_ready()
        page = self._page

        # Verifier login
        if not await self._is_logged_in_twitter(page):
            return {"success": False, "error": "Non connecte sur X. Ouvre Chrome avec le profil et connecte-toi."}

        try:
            # Naviguer vers compose
            await page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Remplir le texte + URL (Twitter auto-embed en quote)
            full_text = f"{text}\n{tweet_url}"
            filled = await self._find_and_fill(page, [
                '[data-testid="tweetTextarea_0"]',
                '[data-testid="tweetTextarea_0_label"]',
                'div[role="textbox"][contenteditable="true"]',
                '.public-DraftEditor-content',
                '[aria-label*="post" i] div[contenteditable]',
                '[aria-label*="tweet" i] div[contenteditable]',
            ], full_text[:280], "Quote tweet textbox")

            if not filled:
                await self._screenshot(page, "quote_tweet_fill_fail")
                return {"success": False, "error": "Impossible de remplir le champ tweet"}

            await page.wait_for_timeout(2000)  # Laisser Twitter embed le quote

            # Cliquer Post — multi-selectors
            posted = await self._find_and_click(page, [
                '[data-testid="tweetButton"]',
                '[data-testid="tweetButtonInline"]',
                'button[role="button"]:has-text("Post")',
                'button[role="button"]:has-text("Poster")',
                'div[role="button"]:has-text("Post")',
                'div[role="button"]:has-text("Poster")',
            ], "Post button")

            if not posted:
                await self._screenshot(page, "quote_tweet_post_fail")
                return {"success": False, "error": "Bouton Post introuvable"}

            await page.wait_for_timeout(5000)
            proof = await self._screenshot(page, "quote_tweet_ok")

            self._record_action("tweet", self._content_hash("tweet", tweet_url))
            return {"success": True, "proof": proof, "text": text[:100], "quoted_url": tweet_url}

        except Exception as e:
            await self._screenshot(page, "quote_tweet_error")
            return {"success": False, "error": str(e)}

    async def post_reddit(self, subreddit: str, title: str, body: str) -> dict:
        """Poste sur un subreddit avec multi-selectors."""
        err = self._check_rate("reddit_post")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("reddit_post", f"{subreddit}:{title}"):
            return {"success": False, "error": "Post Reddit deja fait (doublon)"}

        await self._ensure_ready()
        page = self._page

        try:
            url = f"https://www.reddit.com/r/{subreddit}/submit?type=TEXT"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            # Titre — Reddit 2026 UI: textarea avec placeholder "Title"
            filled_title = False
            for sel in [
                'textarea[placeholder*="Title" i]',
                'input[placeholder*="Title" i]',
                'textarea[name="title"]',
                'input[name="title"]',
                'shreddit-composer textarea',
                '#title-field textarea',
                'div[slot="title"] textarea',
                'faceplate-form textarea',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.click()
                        await el.fill("")
                        await page.keyboard.type(title[:300], delay=20)
                        filled_title = True
                        break
                except Exception:
                    continue

            if not filled_title:
                await self._screenshot(page, "reddit_title_fail")
                return {"success": False, "error": "Champ titre Reddit introuvable"}

            await page.wait_for_timeout(1000)

            # Body — Reddit 2026: contenteditable div ou textarea
            body_filled = False
            for sel in [
                'div[contenteditable="true"][role="textbox"]',
                'div[data-lexical-editor="true"]',
                'div[contenteditable="true"][class*="editor"]',
                'div[contenteditable="true"]',
                '.ql-editor[contenteditable="true"]',
                'textarea[placeholder*="Body" i]',
                'textarea[placeholder*="Text" i]',
                '.public-DraftEditor-content',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.click()
                        await page.keyboard.type(body[:5000], delay=10)
                        body_filled = True
                        break
                except Exception:
                    continue

            if not body_filled:
                print("[BrowserAgent] Reddit body: fallback keyboard type")

            await page.wait_for_timeout(1000)

            # Poster — bouton Post (attendre qu'il soit enabled)
            posted = False
            for sel in [
                'button:has-text("Post"):not([disabled])',
                'button[type="submit"]:has-text("Post")',
                'button:has-text("Submit"):not([disabled])',
                'faceplate-tracker button:has-text("Post")',
                'button[slot="submit-button"]',
                'shreddit-composer button[type="submit"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.click()
                        posted = True
                        break
                except Exception:
                    continue

            if not posted:
                await self._screenshot(page, "reddit_post_fail")
                return {"success": False, "error": "Bouton Post Reddit introuvable"}

            await page.wait_for_timeout(5000)
            proof = await self._screenshot(page, "reddit_ok")

            self._record_action("reddit_post", self._content_hash("reddit_post", f"{subreddit}:{title}"))
            return {"success": True, "proof": proof, "subreddit": subreddit}

        except Exception as e:
            await self._screenshot(page, "reddit_error")
            return {"success": False, "error": str(e)}

    async def search_google(self, query: str, max_results: int = 10) -> list:
        """Recherche Google et extrait les resultats."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(f"https://www.google.com/search?q={query}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            results = []
            # Multi-selectors pour les resultats Google
            for container_sel in ["div.g", "div[data-sokoban-container]", "div.tF2Cxc"]:
                items = await page.locator(container_sel).all()
                if items:
                    for item in items[:max_results]:
                        try:
                            link = item.locator("a").first
                            href = await link.get_attribute("href") or ""
                            title_el = item.locator("h3").first
                            title = await title_el.inner_text() if await title_el.is_visible() else ""
                            if href.startswith("http"):
                                results.append({"title": title, "url": href})
                        except Exception:
                            continue
                    if results:
                        break

            return results[:max_results]

        except Exception as e:
            print(f"[BrowserAgent] Google search error: {e}")
            return []

    async def screenshot_page(self, url: str) -> str:
        """Capture une page complete."""
        await self._ensure_ready()
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await self._page.wait_for_timeout(2000)
            return await self._screenshot(self._page, "screenshot")
        except Exception as e:
            print(f"[BrowserAgent] Screenshot error: {e}")
            return ""

    async def browse_and_extract(self, url: str, selector: str = "body") -> str:
        """Navigue et extrait du contenu avec fallback."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Essayer le selector demande, sinon fallback
            for sel in [selector, "main", "article", "#content", ".content", "body"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        text = await el.inner_text()
                        if text and len(text) > 50:
                            return text[:5000]
                except Exception:
                    continue

            return await page.locator("body").inner_text()

        except Exception as e:
            return f"Error: {e}"


    # ── Twitter Marketing ──

    async def search_twitter(self, query: str, max_results: int = 10) -> list:
        """Cherche des tweets/profils sur X (hashtags, mots-cles)."""
        await self._ensure_ready()
        page = self._page

        try:
            encoded = query.replace(" ", "%20")
            await page.goto(f"https://x.com/search?q={encoded}&src=typed_query&f=live", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            results = []
            # Extraire les tweets du feed
            tweets = await page.locator('article[data-testid="tweet"], article').all()
            for tweet in tweets[:max_results]:
                try:
                    # Username
                    user_el = tweet.locator('a[role="link"] span').first
                    username = await user_el.inner_text() if await user_el.is_visible(timeout=1000) else ""
                    # Texte du tweet
                    text_el = tweet.locator('[data-testid="tweetText"]').first
                    text = await text_el.inner_text() if await text_el.is_visible(timeout=1000) else ""
                    # Lien du tweet
                    time_el = tweet.locator("time").first
                    link_el = time_el.locator("..") if await time_el.is_visible(timeout=1000) else None
                    tweet_url = await link_el.get_attribute("href") if link_el else ""
                    if tweet_url and not tweet_url.startswith("http"):
                        tweet_url = f"https://x.com{tweet_url}"

                    if text:
                        results.append({
                            "username": username,
                            "text": text[:300],
                            "url": tweet_url,
                        })
                except Exception:
                    continue

            print(f"[BrowserAgent] Twitter search '{query}': {len(results)} resultats")
            return results

        except Exception as e:
            print(f"[BrowserAgent] Twitter search error: {e}")
            return []

    async def get_mentions(self, max_results: int = 20) -> list:
        """Lit les notifications/mentions sur X."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto("https://x.com/notifications/mentions", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            mentions = []
            tweets = await page.locator('article[data-testid="tweet"]').all()
            for tweet in tweets[:max_results]:
                try:
                    user_el = tweet.locator('a[role="link"] span').first
                    username = await user_el.inner_text() if await user_el.is_visible(timeout=1000) else ""
                    text_el = tweet.locator('[data-testid="tweetText"]').first
                    text = await text_el.inner_text() if await text_el.is_visible(timeout=1000) else ""
                    time_el = tweet.locator("time").first
                    link_el = time_el.locator("..") if await time_el.is_visible(timeout=1000) else None
                    tweet_url = await link_el.get_attribute("href") if link_el else ""
                    if tweet_url and not tweet_url.startswith("http"):
                        tweet_url = f"https://x.com{tweet_url}"

                    if text:
                        mentions.append({
                            "username": username,
                            "text": text[:300],
                            "url": tweet_url,
                        })
                except Exception:
                    continue

            print(f"[BrowserAgent] Mentions: {len(mentions)}")
            return mentions

        except Exception as e:
            print(f"[BrowserAgent] Mentions error: {e}")
            return []

    async def read_own_tweet_replies(self, max_tweets: int = 3) -> list:
        """Lit les reponses a nos derniers tweets sur @MAXIA_WORLD."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto("https://x.com/MAXIA_WORLD", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            results = []
            # Trouver nos derniers tweets sur le profil
            tweets = await page.locator('article[data-testid="tweet"]').all()

            for tweet in tweets[:max_tweets]:
                try:
                    # Extraire le texte de notre tweet
                    text_el = tweet.locator('[data-testid="tweetText"]').first
                    tweet_text = await text_el.inner_text() if await text_el.is_visible(timeout=1000) else ""

                    # Trouver le lien du tweet via l'element time
                    time_el = tweet.locator("time").first
                    link_el = time_el.locator("..") if await time_el.is_visible(timeout=1000) else None
                    tweet_href = await link_el.get_attribute("href") if link_el else ""
                    if tweet_href and not tweet_href.startswith("http"):
                        tweet_href = f"https://x.com{tweet_href}"

                    if not tweet_href:
                        continue

                    # Cliquer sur le tweet pour ouvrir le thread
                    await tweet.click()
                    await page.wait_for_timeout(3000)

                    # Lire les reponses dans le thread
                    replies = []
                    reply_articles = await page.locator('article[data-testid="tweet"]').all()
                    # Le premier article est notre tweet, les suivants sont les reponses
                    for reply in reply_articles[1:]:
                        try:
                            # Username de la reponse
                            user_el = reply.locator('a[role="link"] span').first
                            username = await user_el.inner_text() if await user_el.is_visible(timeout=1000) else ""
                            # Texte de la reponse
                            reply_text_el = reply.locator('[data-testid="tweetText"]').first
                            reply_text = await reply_text_el.inner_text() if await reply_text_el.is_visible(timeout=1000) else ""
                            # URL de la reponse
                            reply_time = reply.locator("time").first
                            reply_link = reply_time.locator("..") if await reply_time.is_visible(timeout=1000) else None
                            reply_url = await reply_link.get_attribute("href") if reply_link else ""
                            if reply_url and not reply_url.startswith("http"):
                                reply_url = f"https://x.com{reply_url}"

                            if reply_text:
                                replies.append({
                                    "username": username,
                                    "text": reply_text[:300],
                                    "url": reply_url,
                                })
                        except Exception:
                            continue

                    results.append({
                        "tweet_text": tweet_text[:300],
                        "replies": replies,
                    })

                    # Revenir au profil pour le tweet suivant
                    await page.goto("https://x.com/MAXIA_WORLD", wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3000)

                except Exception:
                    # Si erreur sur un tweet, revenir au profil et continuer
                    try:
                        await page.goto("https://x.com/MAXIA_WORLD", wait_until="domcontentloaded", timeout=20000)
                        await page.wait_for_timeout(3000)
                    except Exception:
                        pass
                    continue

            print(f"[BrowserAgent] Own tweet replies: {len(results)} tweets scanned")
            return results

        except Exception as e:
            print(f"[BrowserAgent] Read own tweet replies error: {e}")
            return []

    async def like_tweet(self, tweet_url: str) -> dict:
        """Like un tweet pour gagner en visibilite."""
        err = self._check_rate("like")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("like", tweet_url):
            return {"success": False, "error": "Deja like"}
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            liked = await self._find_and_click(page, [
                '[data-testid="like"]',
                'button[aria-label*="Like" i]',
                'button[aria-label*="Aimer" i]',
                'div[role="button"][data-testid="like"]',
            ], "Like button")

            if not liked:
                return {"success": False, "error": "Bouton Like introuvable"}

            await page.wait_for_timeout(1000)
            self._record_action("like", self._content_hash("like", tweet_url))
            return {"success": True, "url": tweet_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def follow_user(self, username: str) -> dict:
        """Follow un utilisateur sur X."""
        err = self._check_rate("follow")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("follow", username):
            return {"success": False, "error": f"Deja follow: {username}"}
        await self._ensure_ready()
        page = self._page

        try:
            clean = username.lstrip("@")
            await page.goto(f"https://x.com/{clean}", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Verifier si deja follow
            unfollow_btn = page.locator('[data-testid$="-unfollow"]').first
            if await unfollow_btn.is_visible(timeout=2000):
                return {"success": True, "already": True, "username": clean}

            followed = await self._find_and_click(page, [
                '[data-testid$="-follow"]',
                '[data-testid="placementTracking"] [role="button"]',
                'button[aria-label*="Follow @" i]',
                'button[aria-label*="Suivre @" i]',
                'button[aria-label*="Follow" i]:not([aria-label*="Following"])',
                'div[role="button"]:has-text("Follow"):not(:has-text("Following"))',
                'div[role="button"]:has-text("Suivre"):not(:has-text("Suivi"))',
            ], "Follow button", timeout=3000)

            if not followed:
                await self._screenshot(page, "follow_fail")
                return {"success": False, "error": "Bouton Follow introuvable"}

            await page.wait_for_timeout(1000)
            self._record_action("follow", self._content_hash("follow", clean))
            return {"success": True, "username": clean}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def search_twitter_profiles(self, query: str, max_results: int = 10) -> list:
        """Cherche des profils sur X (onglet People)."""
        await self._ensure_ready()
        page = self._page

        try:
            encoded = query.replace(" ", "%20")
            await page.goto(f"https://x.com/search?q={encoded}&src=typed_query&f=user", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            profiles = []
            cells = await page.locator('[data-testid="UserCell"]').all()
            for cell in cells[:max_results]:
                try:
                    name_el = cell.locator('a[role="link"] span').first
                    name = await name_el.inner_text() if await name_el.is_visible(timeout=1000) else ""
                    link_el = cell.locator('a[role="link"]').first
                    href = await link_el.get_attribute("href") if await link_el.is_visible(timeout=1000) else ""
                    bio_el = cell.locator('[dir="auto"]').last
                    bio = await bio_el.inner_text() if await bio_el.is_visible(timeout=1000) else ""

                    if name:
                        profiles.append({
                            "name": name,
                            "url": f"https://x.com{href}" if href and not href.startswith("http") else href,
                            "bio": bio[:200],
                        })
                except Exception:
                    continue

            print(f"[BrowserAgent] Twitter profiles '{query}': {len(profiles)}")
            return profiles

        except Exception as e:
            print(f"[BrowserAgent] Twitter profiles error: {e}")
            return []

    # ── Reddit Marketing ──

    async def _extract_reddit_posts(self, page, max_results: int) -> list:
        """Extrait les posts Reddit de la page courante (fonctionne pour search et /new/)."""
        results = []
        # New Reddit (2026): posts sont dans des elements varies
        for post_sel in [
            'shreddit-post', 'article', 'faceplate-tracker',
            'div[data-testid="post-container"]', 'div.Post',
            'a[data-testid="post-title"]',
        ]:
            posts = await page.locator(post_sel).all()
            if len(posts) > 0:
                for post in posts[:max_results]:
                    try:
                        # Chercher le titre dans plusieurs endroits
                        title = ""
                        href = ""
                        for t_sel in ['a[slot="title"]', 'a[data-click-id="body"]', 'h3', 'a.title', '[slot="title"]', 'a[slot="full-post-link"]']:
                            t_el = post.locator(t_sel).first
                            if await t_el.is_visible(timeout=500):
                                title = await t_el.inner_text()
                                href = await t_el.get_attribute("href") or ""
                                break
                        # Fallback: tout le texte du post
                        if not title:
                            title = (await post.inner_text())[:100]
                        if href and not href.startswith("http"):
                            href = f"https://www.reddit.com{href}"
                        if title and len(title) > 5:
                            results.append({"title": title[:200], "url": href})
                    except Exception:
                        continue
                if results:
                    break

        # Fallback: extraire les liens de la page
        if not results:
            links = await page.locator('a[href*="/comments/"]').all()
            for link in links[:max_results]:
                try:
                    title = await link.inner_text()
                    href = await link.get_attribute("href") or ""
                    if title and len(title) > 5 and href:
                        if not href.startswith("http"):
                            href = f"https://www.reddit.com{href}"
                        results.append({"title": title[:200], "url": href})
                except Exception:
                    continue
        return results

    async def search_reddit(self, subreddit: str, query: str, max_results: int = 10) -> list:
        """Cherche des posts sur un subreddit (new Reddit UI 2026).
        Strategie: 1) search query, 2) fallback browse /new/ du subreddit."""
        await self._ensure_ready()
        page = self._page

        try:
            encoded = query.replace(" ", "+")
            # Strategie 1: recherche avec query
            await page.goto(f"https://www.reddit.com/r/{subreddit}/search/?q={encoded}&restrict_sr=1&sort=new&type=link", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)

            results = await self._extract_reddit_posts(page, max_results)

            # Strategie 2: si la recherche ne donne rien, parcourir les posts recents du sub
            if not results:
                print(f"[BrowserAgent] Reddit search empty for '{query}', falling back to r/{subreddit}/new/")
                await page.goto(f"https://www.reddit.com/r/{subreddit}/new/", wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(4000)
                results = await self._extract_reddit_posts(page, max_results)

            # Strategie 3: essayer old.reddit.com qui a un HTML plus simple
            if not results:
                print(f"[BrowserAgent] Reddit new/ empty, trying old.reddit.com")
                await page.goto(f"https://old.reddit.com/r/{subreddit}/new/", wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)
                links = await page.locator('a.title').all()
                for link in links[:max_results]:
                    try:
                        title = await link.inner_text()
                        href = await link.get_attribute("href") or ""
                        if title and len(title) > 5:
                            if href and not href.startswith("http"):
                                href = f"https://old.reddit.com{href}"
                            # Convertir old.reddit.com en www.reddit.com pour commenter
                            if href:
                                href = href.replace("old.reddit.com", "www.reddit.com")
                            results.append({"title": title[:200], "url": href})
                    except Exception:
                        continue

            print(f"[BrowserAgent] Reddit search r/{subreddit} '{query}': {len(results)}")
            return results

        except Exception as e:
            print(f"[BrowserAgent] Reddit search error: {e}")
            return []

    async def comment_reddit(self, post_url: str, text: str) -> dict:
        """Commente sur un post Reddit (new Reddit UI 2026)."""
        err = self._check_rate("reddit_comment")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("reddit_comment", post_url):
            return {"success": False, "error": "Deja commente sur ce post"}
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            # D'abord cliquer sur la zone commentaire pour l'activer
            for trigger in [
                'div[placeholder*="Add a comment" i]',
                'shreddit-composer',
                'button:has-text("Add a comment")',
                'button:has-text("Ajouter un commentaire")',
                '[data-click-id="comment"]',
                'faceplate-tracker[source="comment"]',
                'p[placeholder*="comment" i]',
            ]:
                try:
                    el = page.locator(trigger).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await page.wait_for_timeout(1500)
                        break
                except Exception:
                    continue

            # Remplir le commentaire
            filled = await self._find_and_fill(page, [
                'div[contenteditable="true"][role="textbox"]',
                'div[contenteditable="true"]',
                'shreddit-composer div[contenteditable="true"]',
                'textarea[placeholder*="comment" i]',
                'textarea[name="comment"]',
                'div[data-placeholder*="comment" i]',
            ], text[:5000], "Reddit comment box")

            if not filled:
                # Dernier recours: taper au clavier
                try:
                    await page.keyboard.type(text[:2000], delay=20)
                    filled = True
                except Exception:
                    pass

            if not filled:
                await self._screenshot(page, "reddit_comment_fail")
                return {"success": False, "error": "Champ commentaire Reddit introuvable"}

            await page.wait_for_timeout(1000)

            # Poster le commentaire
            posted = await self._find_and_click(page, [
                'button:has-text("Comment")',
                'button:has-text("Commenter")',
                'button[type="submit"]:has-text("Comment")',
                'faceplate-tracker button:has-text("Comment")',
            ], "Comment button")

            if not posted:
                await self._screenshot(page, "reddit_comment_btn_fail")
                return {"success": False, "error": "Bouton Comment introuvable"}

            await page.wait_for_timeout(5000)
            proof = await self._screenshot(page, "reddit_comment_ok")
            self._record_action("reddit_comment", self._content_hash("reddit_comment", post_url))
            return {"success": True, "proof": proof, "url": post_url}

        except Exception as e:
            await self._screenshot(page, "reddit_comment_error")
            return {"success": False, "error": str(e)}

    async def upvote_reddit(self, post_url: str) -> dict:
        """Upvote un post Reddit."""
        err = self._check_rate("reddit_upvote")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("reddit_upvote", post_url):
            return {"success": False, "error": "Deja upvote"}

        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            upvoted = await self._find_and_click(page, [
                'button[aria-label*="upvote" i]',
                'button[aria-label*="Vote" i]',
                '[data-click-id="upvote"]',
                'shreddit-post button[aria-label*="upvote" i]',
                'button[icon-name="upvote-outline"]',
                'div[data-click-id="upvote"] button',
            ], "Upvote button")

            if not upvoted:
                await self._screenshot(page, "reddit_upvote_fail")
                return {"success": False, "error": "Bouton upvote Reddit introuvable"}

            await page.wait_for_timeout(1500)
            proof = await self._screenshot(page, "reddit_upvote_ok")
            self._record_action("reddit_upvote", self._content_hash("reddit_upvote", post_url))
            return {"success": True, "proof": proof, "url": post_url}

        except Exception as e:
            await self._screenshot(page, "reddit_upvote_error")
            return {"success": False, "error": str(e)}


    # ── Prospect Scoring ──

    async def score_twitter_profile(self, username: str) -> dict:
        """Score un profil Twitter pour savoir s'il vaut la peine d'etre contacte.
        Score 0-100 base sur : bio, followers, activite, pertinence AI/crypto."""
        await self._ensure_ready()
        page = self._page

        try:
            clean = username.lstrip("@")
            await page.goto(f"https://x.com/{clean}", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            score = 0
            details = {}

            # Bio
            bio = ""
            for sel in ['div[data-testid="UserDescription"]', '[data-testid="UserDescription"] span']:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    bio = await el.inner_text()
                    break
            details["bio"] = bio[:200]

            # Mots-cles pertinents dans la bio
            keywords_high = ["AI agent", "solana", "web3", "developer", "dev", "builder", "python", "rust", "blockchain", "defi", "bot"]
            keywords_mid = ["crypto", "nft", "ethereum", "coding", "software", "engineer", "startup", "founder"]
            bio_lower = bio.lower()
            for kw in keywords_high:
                if kw in bio_lower:
                    score += 15
            for kw in keywords_mid:
                if kw in bio_lower:
                    score += 8

            # Followers count
            try:
                followers_el = page.locator('a[href$="/verified_followers"] span, a[href$="/followers"] span').first
                followers_text = await followers_el.inner_text() if await followers_el.is_visible(timeout=2000) else "0"
                followers_text = followers_text.replace(",", "").replace(".", "").strip()
                if "K" in followers_text.upper():
                    followers = int(float(followers_text.upper().replace("K", "")) * 1000)
                elif "M" in followers_text.upper():
                    followers = int(float(followers_text.upper().replace("M", "")) * 1000000)
                else:
                    followers = int(followers_text) if followers_text.isdigit() else 0
                details["followers"] = followers

                # Sweet spot: 100-10000 followers (pas un bot, pas un gros compte inaccessible)
                if 100 <= followers <= 1000:
                    score += 20
                elif 1000 < followers <= 10000:
                    score += 15
                elif 50 <= followers < 100:
                    score += 10
                elif followers > 10000:
                    score += 5  # Trop gros, peu de chance de reponse
            except Exception:
                details["followers"] = 0

            # "no revenue" signals dans la bio
            frustration_kw = ["no revenue", "side project", "building", "shipping", "0 users", "looking for"]
            for kw in frustration_kw:
                if kw in bio_lower:
                    score += 10

            details["score"] = min(score, 100)
            details["username"] = clean
            details["recommend"] = "follow+engage" if score >= 40 else "like only" if score >= 20 else "skip"

            return details

        except Exception as e:
            return {"username": username, "score": 0, "error": str(e), "recommend": "skip"}

    # ── Twitter DMs ──

    async def dm_twitter(self, username: str, text: str) -> dict:
        """Envoie un DM sur X."""
        err = self._check_rate("dm")
        if err:
            return {"success": False, "error": err}
        if self._is_duplicate("dm", username):
            return {"success": False, "error": f"DM deja envoye a {username}"}
        await self._ensure_ready()
        page = self._page

        try:
            clean = username.lstrip("@")
            await self._goto_dms(page)

            # Nouveau message
            clicked = await self._find_and_click(page, [
                '[data-testid="NewDM_Button"]',
                'a[href="/messages/compose"]',
                '[aria-label*="New message" i]',
                '[aria-label*="Nouveau message" i]',
            ], "New DM button")
            if not clicked:
                return {"success": False, "error": "Bouton nouveau DM introuvable"}
            await page.wait_for_timeout(1500)

            # Chercher le destinataire
            filled = await self._find_and_fill(page, [
                'input[data-testid="searchPeople"]',
                'input[placeholder*="Search" i]',
                'input[placeholder*="Rechercher" i]',
                'input[aria-label*="Search" i]',
            ], clean, "DM search")
            if not filled:
                return {"success": False, "error": "Champ recherche DM introuvable"}
            await page.wait_for_timeout(2000)

            # Cliquer sur le profil dans les resultats
            result_item = page.locator(f'[data-testid="typeaheadResult"]').first
            if await result_item.is_visible(timeout=5000):
                await result_item.click()
            else:
                return {"success": False, "error": f"Profil {clean} introuvable dans les DMs"}
            await page.wait_for_timeout(1000)

            # Cliquer Next/Suivant
            await self._find_and_click(page, [
                'button[data-testid="nextButton"]',
                'button:has-text("Next")',
                'button:has-text("Suivant")',
            ], "Next button")
            await page.wait_for_timeout(1000)

            # Taper le message
            filled = await self._find_and_fill(page, [
                '[data-testid="dmComposerTextInput"]',
                'div[data-testid="dmComposerTextInput"]',
                'div[role="textbox"][contenteditable]',
            ], text[:1000], "DM text")
            if not filled:
                return {"success": False, "error": "Champ message DM introuvable"}
            await page.wait_for_timeout(500)

            # Envoyer
            sent = await self._find_and_click(page, [
                '[data-testid="dmComposerSendButton"]',
                'button[aria-label*="Send" i]',
                'button[aria-label*="Envoyer" i]',
            ], "Send DM")
            if not sent:
                return {"success": False, "error": "Bouton envoyer DM introuvable"}

            await page.wait_for_timeout(2000)
            self._record_action("dm", self._content_hash("dm", username))
            return {"success": True, "username": clean}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Telegram Web ──

    async def send_telegram(self, group_or_user: str, text: str) -> dict:
        """Envoie un message sur Telegram Web (groupe ou user)."""
        err = self._check_rate("dm")
        if err:
            return {"success": False, "error": err}
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto("https://web.telegram.org/a/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            # Cliquer directement sur le chat dans la liste (plus fiable)
            found = False
            items = await page.locator('.ListItem').all()
            for item in items[:15]:
                try:
                    item_text = await item.inner_text()
                    if group_or_user.lower() in item_text.lower():
                        await item.click()
                        found = True
                        break
                except Exception:
                    continue

            # Fallback: recherche
            if not found:
                search = page.locator('#telegram-search-input, input[placeholder*="Search" i]').first
                if await search.is_visible(timeout=3000):
                    await search.click()
                    await search.fill(group_or_user)
                    await page.wait_for_timeout(3000)
                    items2 = await page.locator('.ListItem').all()
                    for item in items2[:5]:
                        try:
                            item_text = await item.inner_text()
                            if group_or_user.lower() in item_text.lower():
                                await item.click()
                                found = True
                                break
                        except Exception:
                            continue

            if not found:
                return {"success": False, "error": f"Groupe/user '{group_or_user}' introuvable"}

            await page.wait_for_timeout(2000)

            # Taper le message
            filled = await self._find_and_fill(page, [
                'div.input-message-input[contenteditable="true"]',
                '#editable-message-text',
                'div[contenteditable="true"]',
            ], text[:4000], "Telegram message")
            if not filled:
                return {"success": False, "error": "Champ message Telegram introuvable"}

            # Envoyer (Enter)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            self._record_action("dm", self._content_hash("telegram", f"{group_or_user}:{text[:50]}"))
            return {"success": True, "target": group_or_user}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def join_telegram_group(self, group_link: str) -> dict:
        """Rejoint un groupe Telegram public."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(group_link, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)

            # Cliquer Join (timeout court pour ne pas bloquer)
            joined = await self._find_and_click(page, [
                'button:has-text("Join Group")',
                'button:has-text("Join Channel")',
                'button:has-text("JOIN")',
                '.btn-primary:has-text("Join")',
                'a.tgme_action_button_new:has-text("Join")',
                'a.tgme_action_button_new',
            ], "Join button", timeout=3000)

            if joined:
                await page.wait_for_timeout(2000)
            return {"success": joined, "group": group_link}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── GitHub ──

    async def star_github_repo(self, repo_url: str) -> dict:
        """Star un repo GitHub."""
        if self._is_duplicate("star", repo_url):
            return {"success": False, "error": "Deja star"}
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(repo_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Verifier si deja starred
            unstar = page.locator('button:has-text("Unstar"), button[aria-label*="Unstar"]').first
            if await unstar.is_visible(timeout=2000):
                return {"success": True, "already": True, "repo": repo_url}

            starred = await self._find_and_click(page, [
                'button:has-text("Star")',
                'button[aria-label*="Star this"]',
                '.starring-container button:not(.starred)',
            ], "Star button")

            if starred:
                self._record_action("star", self._content_hash("star", repo_url))
            return {"success": starred, "repo": repo_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def post_github_issue(self, repo_url: str, title: str, body: str) -> dict:
        """Cree une issue sur un repo GitHub."""
        await self._ensure_ready()
        page = self._page

        try:
            issues_url = repo_url.rstrip("/") + "/issues/new"
            await page.goto(issues_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # Titre
            filled = await self._find_and_fill(page, [
                'input#issue_title',
                'input[name="issue[title]"]',
                'input[placeholder*="Title" i]',
            ], title[:256], "Issue title")
            if not filled:
                return {"success": False, "error": "Champ titre issue introuvable"}

            # Body
            await self._find_and_fill(page, [
                'textarea#issue_body',
                'textarea[name="issue[body]"]',
                'textarea[placeholder*="Leave a comment" i]',
                'div[contenteditable="true"]',
            ], body[:5000], "Issue body")

            await page.wait_for_timeout(1000)

            # Submit
            submitted = await self._find_and_click(page, [
                'button:has-text("Submit new issue")',
                'button[type="submit"]:has-text("Submit")',
            ], "Submit issue")

            await page.wait_for_timeout(5000)
            return {"success": submitted, "repo": repo_url, "title": title[:50]}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def comment_github_discussion(self, discussion_url: str, text: str) -> dict:
        """Commente sur une discussion/issue GitHub via Playwright.
        Echoue silencieusement si les selectors ne matchent pas (UI peut varier)."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(discussion_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            # Verifier qu'on est bien sur une page GitHub avec un champ commentaire
            # (si pas connecte ou page invalide, on skip)
            logged_in = await page.locator('meta[name="user-login"]').count() > 0
            if not logged_in:
                # Verifier via un autre indicateur
                logged_in = await page.locator('.Header-link--current-user, [data-login]').count() > 0
            if not logged_in:
                return {"success": False, "error": "Not logged in to GitHub"}

            filled = await self._find_and_fill(page, [
                'textarea#new_comment_field',
                'textarea[name="comment[body]"]',
                'textarea[placeholder*="Leave a comment" i]',
                'textarea[placeholder*="Add your comment" i]',
                'div.CommentBox-container textarea',
                'textarea.js-comment-field',
            ], text[:5000], "GitHub comment")
            if not filled:
                return {"success": False, "error": "GitHub comment field not found"}

            await page.wait_for_timeout(1000)

            submitted = await self._find_and_click(page, [
                'button:has-text("Comment")',
                'button[type="submit"]:has-text("Comment")',
                'button.btn-primary:has-text("Comment")',
            ], "Comment button")

            await page.wait_for_timeout(5000)
            return {"success": submitted, "url": discussion_url}

        except Exception as e:
            print(f"[BrowserAgent] GitHub comment failed (expected without login): {e}")
            return {"success": False, "error": str(e)}

    # ── Discord Web ──

    async def send_discord(self, server_or_channel_url: str, text: str) -> dict:
        """Envoie un message sur Discord Web. Accepte invite URL ou channel URL."""
        err = self._check_rate("dm")
        if err:
            return {"success": False, "error": err}
        if not text:
            return {"success": False, "error": "Empty text"}
        await self._ensure_ready()
        page = self._page

        try:
            # Si c'est un invite link (discord.gg/...), aller sur Discord app
            # et trouver le channel general du serveur
            if "discord.gg/" in server_or_channel_url or "discord.com/invite/" in server_or_channel_url:
                # D'abord naviguer vers l'invite pour arriver sur le serveur
                await page.goto(server_or_channel_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(5000)
                # Cliquer "Accept Invite" si demandé
                for sel in ['button:has-text("Accept Invite")', 'button:has-text("Accepter l\'invitation")',
                            'button:has-text("Join")', 'button:has-text("Continue to Discord")']:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                            await page.wait_for_timeout(3000)
                            break
                    except Exception:
                        continue
                # Attendre d'etre sur discord.com/channels/...
                await page.wait_for_timeout(3000)
            elif "discord.com/channels/" in server_or_channel_url:
                await page.goto(server_or_channel_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(5000)
            else:
                # Aller sur Discord app directement
                await page.goto("https://discord.com/channels/@me", wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)

            # Chercher et remplir le champ de message
            filled = await self._find_and_fill(page, [
                'div[role="textbox"][contenteditable="true"]',
                'div[data-slate-editor="true"]',
                'div.slateTextArea-1Mkdgw',
            ], text[:2000], "Discord message")
            if not filled:
                return {"success": False, "error": "Champ message Discord introuvable (pas connecte?)"}

            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            self._record_action("dm", self._content_hash("discord", f"{server_or_channel_url}:{text[:50]}"))
            return {"success": True, "channel": server_or_channel_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def join_discord_server(self, invite_link: str) -> dict:
        """Rejoint un serveur Discord via invite."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(invite_link, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            joined = await self._find_and_click(page, [
                'button:has-text("Accept Invite")',
                'button:has-text("Join")',
                'button:has-text("Accepter l\'invitation")',
            ], "Join Discord")

            await page.wait_for_timeout(5000)
            return {"success": joined, "invite": invite_link}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Marketing avance ──

    async def detect_opportunities(self, max_results: int = 5) -> list:
        """Detecte des prospects chauds: devs qui se plaignent de 0 users/revenue."""
        queries = [
            '"my bot" "no users" OR "no revenue" OR "0 clients"',
            '"AI agent" "looking for" users OR clients OR customers',
            '"built a bot" BUT "no one uses"',
            'solana bot "side project" no revenue',
        ]
        opportunities = []
        for q in queries[:2]:  # Max 2 queries pour eviter le rate limit
            tweets = await self.search_twitter(q, 5)
            for t in tweets:
                if t.get("url"):
                    opportunities.append({
                        "query": q[:40],
                        "username": t.get("username", ""),
                        "text": t.get("text", "")[:200],
                        "url": t.get("url", ""),
                    })
            if opportunities:
                break
        print(f"[BrowserAgent] Opportunities: {len(opportunities)}")
        return opportunities[:max_results]

    async def verify_tweet_engagement(self, tweet_url: str) -> dict:
        """Verifie l'engagement d'un tweet poste (likes, retweets, replies)."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            engagement = {"url": tweet_url, "likes": 0, "retweets": 0, "replies": 0}

            for metric, selectors in [
                ("likes", ['[data-testid="like"] span', 'button[aria-label*="Like"] span']),
                ("retweets", ['[data-testid="retweet"] span', 'button[aria-label*="Repost"] span']),
                ("replies", ['[data-testid="reply"] span', 'button[aria-label*="Repl"] span']),
            ]:
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=1000):
                            text = await el.inner_text()
                            text = text.strip().replace(",", "")
                            if text and text[0].isdigit():
                                engagement[metric] = int(text)
                            break
                    except Exception:
                        continue

            return engagement

        except Exception as e:
            return {"url": tweet_url, "error": str(e)}

    async def post_thread(self, tweets: list) -> dict:
        """Poste un thread Twitter (liste de tweets chaines)."""
        if not tweets or len(tweets) < 2:
            return {"success": False, "error": "Un thread necessite au moins 2 tweets"}

        err = self._check_rate("tweet")
        if err:
            return {"success": False, "error": err}

        await self._ensure_ready()
        page = self._page

        try:
            # Premier tweet
            await page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            await self._find_and_fill(page, [
                '[data-testid="tweetTextarea_0"]',
                'div[role="textbox"][contenteditable="true"]',
            ], tweets[0][:280], "Thread first tweet")
            await page.wait_for_timeout(500)

            # Ajouter les tweets suivants via le bouton "+"
            for i, text in enumerate(tweets[1:], 1):
                # Cliquer "Add another post"
                add_btn = page.locator('[data-testid="addButton"], button[aria-label*="Add"]').first
                if await add_btn.is_visible(timeout=3000):
                    await add_btn.click()
                    await page.wait_for_timeout(1000)

                    # Remplir le tweet suivant
                    textarea = page.locator(f'[data-testid="tweetTextarea_{i}"]').first
                    if await textarea.is_visible(timeout=2000):
                        await textarea.click()
                        await textarea.fill(text[:280])
                    await page.wait_for_timeout(500)

            # Poster le thread
            posted = await self._find_and_click(page, [
                '[data-testid="tweetButton"]',
                'button:has-text("Post all")',
                'button:has-text("Tout poster")',
            ], "Post thread")

            if posted:
                await page.wait_for_timeout(5000)
                for t in tweets:
                    self._record_action("tweet", self._content_hash("tweet", t))
                return {"success": True, "tweets": len(tweets)}
            return {"success": False, "error": "Bouton Post introuvable"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def scrape_competitor_followers(self, competitor: str, max_results: int = 10) -> list:
        """Liste les followers d'un concurrent pour trouver des prospects."""
        await self._ensure_ready()
        page = self._page

        try:
            clean = competitor.lstrip("@")
            await page.goto(f"https://x.com/{clean}/followers", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            followers = []
            cells = await page.locator('[data-testid="UserCell"]').all()
            for cell in cells[:max_results]:
                try:
                    name_el = cell.locator('a[role="link"] span').first
                    name = await name_el.inner_text() if await name_el.is_visible(timeout=1000) else ""
                    link_el = cell.locator('a[role="link"]').first
                    href = await link_el.get_attribute("href") if link_el else ""
                    bio_el = cell.locator('[dir="auto"]').last
                    bio = await bio_el.inner_text() if await bio_el.is_visible(timeout=1000) else ""
                    username = href.split("/")[-1] if href else ""

                    if name and username:
                        followers.append({"name": name, "username": username, "bio": bio[:150]})
                except Exception:
                    continue

            print(f"[BrowserAgent] Followers @{clean}: {len(followers)}")
            return followers

        except Exception as e:
            print(f"[BrowserAgent] Scrape followers error: {e}")
            return []

    # ── Conversation Manager (DM inbox) ──

    async def read_twitter_dms(self, max_conversations: int = 10) -> list:
        """Lit la boite de reception DMs Twitter.
        Strategie: lire les noms et previews directement dans le sidebar,
        sans cliquer dans chaque conversation (evite le probleme de click React)."""
        await self._ensure_ready()
        page = self._page

        try:
            await self._goto_dms(page)

            conversations = []
            # Lister les conversations (nouvelle UI X 2026)
            convs = await page.locator('[data-testid^="dm-conversation-item-"]').all()
            if not convs:
                convs = await page.locator('[data-testid="conversation"]').all()
            if not convs:
                # Fallback: tous les items dans la liste DM sidebar
                convs = await page.locator('[data-testid="cellInnerDiv"]').all()

            for conv in convs[:max_conversations]:
                try:
                    name = ""
                    preview = ""

                    # Extraire le nom : premier span avec du texte significatif
                    # (dans le sidebar DM, le nom est souvent le premier span visible)
                    spans = await conv.locator('span[dir="auto"], span[dir="ltr"]').all()
                    for span in spans:
                        try:
                            txt = await span.inner_text(timeout=500)
                            txt = txt.strip()
                            if not txt or len(txt) < 2:
                                continue
                            # Le nom est le premier texte court (pas le preview du message)
                            if not name and len(txt) < 50 and not txt.startswith("@"):
                                name = txt
                            elif name and not preview:
                                # Le preview est le texte suivant
                                preview = txt
                                break
                        except Exception:
                            continue

                    # Fallback: tout le texte visible
                    if not name:
                        try:
                            all_text = await conv.inner_text(timeout=1000)
                            lines = [l.strip() for l in all_text.split("\n") if l.strip()]
                            if lines:
                                name = lines[0][:50]
                            if len(lines) > 1:
                                preview = lines[-1][:100]
                        except Exception:
                            pass

                    # Indicateur non lu (badge, dot, ou texte "unread")
                    unread = False
                    try:
                        unread = await conv.locator('[aria-label*="unread" i], [data-testid="unread"], [class*="unread" i]').count() > 0
                        if not unread:
                            # Certaines UI utilisent un badge colore
                            unread = await conv.locator('[aria-label*="new" i]').count() > 0
                    except Exception:
                        pass

                    if name:
                        conversations.append({
                            "name": name[:50],
                            "preview": preview[:100],
                            "unread": unread,
                        })
                except Exception:
                    continue

            print(f"[BrowserAgent] Twitter DMs: {len(conversations)} conversations")
            return conversations

        except Exception as e:
            print(f"[BrowserAgent] Read DMs error: {e}")
            return []

    async def read_twitter_dm_conversation(self, contact_name: str) -> list:
        """Lit les messages d'une conversation DM specifique.
        Utilise plusieurs methodes de click pour ouvrir la conversation."""
        await self._ensure_ready()
        page = self._page

        try:
            await self._goto_dms(page)

            # Trouver la conversation par nom
            opened = False
            conv_selectors = [
                f'[data-testid^="dm-conversation-item-"]:has-text("{contact_name}")',
                f'[data-testid="conversation"]:has-text("{contact_name}")',
                f'[data-testid="cellInnerDiv"]:has-text("{contact_name}")',
            ]
            for sel in conv_selectors:
                conv = page.locator(sel).first
                if not await conv.is_visible(timeout=2000):
                    continue

                # Methode 1: mouse.click au centre exact (simule clic humain)
                try:
                    box = await conv.bounding_box()
                    if box:
                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        await page.wait_for_timeout(3000)
                        if await page.locator('[data-testid="messageEntry"], [data-testid="dm-conversation-panel"], [data-testid="dmComposerTextInput"]').first.is_visible(timeout=2000):
                            opened = True
                            break
                except Exception:
                    pass

                # Methode 2: click force
                try:
                    await conv.click(force=True)
                    await page.wait_for_timeout(3000)
                    if await page.locator('[data-testid="messageEntry"], [data-testid="dm-conversation-panel"], [data-testid="dmComposerTextInput"]').first.is_visible(timeout=2000):
                        opened = True
                        break
                except Exception:
                    pass

                # Methode 3: dispatchEvent click
                try:
                    await conv.dispatch_event("click")
                    await page.wait_for_timeout(3000)
                    if await page.locator('[data-testid="messageEntry"], [data-testid="dm-conversation-panel"], [data-testid="dmComposerTextInput"]').first.is_visible(timeout=2000):
                        opened = True
                        break
                except Exception:
                    pass

                # Methode 4: focus + Enter
                try:
                    await conv.focus()
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(3000)
                    if await page.locator('[data-testid="messageEntry"], [data-testid="dm-conversation-panel"], [data-testid="dmComposerTextInput"]').first.is_visible(timeout=2000):
                        opened = True
                        break
                except Exception:
                    pass

                break  # Tried all methods on this selector

            if not opened:
                print(f"[BrowserAgent] Could not open DM conversation with {contact_name}")
                return []

            # Lire les messages
            messages = []
            msg_els = await page.locator('[data-testid="messageEntry"], [data-testid="tweetText"]').all()
            for msg in msg_els[-10:]:  # 10 derniers messages
                try:
                    text = await msg.inner_text()
                    if text:
                        messages.append(text[:500])
                except Exception:
                    continue

            return messages

        except Exception as e:
            print(f"[BrowserAgent] Read DM conversation error: {e}")
            return []

    async def reply_twitter_dm(self, contact_name: str, text: str) -> dict:
        """Repond dans une conversation DM Twitter existante.
        Utilise plusieurs methodes de click pour ouvrir la conversation,
        puis plusieurs strategies pour remplir et envoyer."""
        err = self._check_rate("dm")
        if err:
            return {"success": False, "error": err}
        await self._ensure_ready()
        page = self._page

        try:
            await self._goto_dms(page)

            # Trouver et cliquer sur la conversation avec plusieurs methodes
            opened = False
            conv_selectors = [
                f'[data-testid^="dm-conversation-item-"]:has-text("{contact_name}")',
                f'[data-testid="conversation"]:has-text("{contact_name}")',
                f'[data-testid="cellInnerDiv"]:has-text("{contact_name}")',
            ]
            for sel in conv_selectors:
                conv = page.locator(sel).first
                if not await conv.is_visible(timeout=2000):
                    continue

                # Methode 1: mouse.click au centre exact
                try:
                    box = await conv.bounding_box()
                    if box:
                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        await page.wait_for_timeout(3000)
                except Exception:
                    pass

                # Verifier si un champ de saisie DM est apparu
                composer_visible = False
                for cs in ['[data-testid="dm-composer-textarea"]', '[data-testid="dmComposerTextInput"]', 'div[role="textbox"][contenteditable]']:
                    if await page.locator(cs).first.is_visible(timeout=1500):
                        composer_visible = True
                        break

                if composer_visible:
                    opened = True
                    break

                # Methode 2: click force
                try:
                    await conv.click(force=True)
                    await page.wait_for_timeout(3000)
                    for cs in ['[data-testid="dm-composer-textarea"]', '[data-testid="dmComposerTextInput"]', 'div[role="textbox"][contenteditable]']:
                        if await page.locator(cs).first.is_visible(timeout=1500):
                            opened = True
                            break
                    if opened:
                        break
                except Exception:
                    pass

                # Methode 3: dispatchEvent
                try:
                    await conv.dispatch_event("click")
                    await page.wait_for_timeout(3000)
                    for cs in ['[data-testid="dm-composer-textarea"]', '[data-testid="dmComposerTextInput"]', 'div[role="textbox"][contenteditable]']:
                        if await page.locator(cs).first.is_visible(timeout=1500):
                            opened = True
                            break
                    if opened:
                        break
                except Exception:
                    pass

                # Methode 4: focus + Enter
                try:
                    await conv.focus()
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(3000)
                    for cs in ['[data-testid="dm-composer-textarea"]', '[data-testid="dmComposerTextInput"]', 'div[role="textbox"][contenteditable]']:
                        if await page.locator(cs).first.is_visible(timeout=1500):
                            opened = True
                            break
                    if opened:
                        break
                except Exception:
                    pass

                break  # Tried all methods on this selector

            if not opened:
                return {"success": False, "error": f"Could not open conversation with {contact_name}"}

            # Taper et envoyer (nouvelle UI X 2026: dm-composer-*)
            filled = await self._find_and_fill(page, [
                '[data-testid="dm-composer-textarea"]',
                '[data-testid="dmComposerTextInput"]',
                'div[data-testid="dm-composer-input-container"] div[contenteditable]',
                'div[role="textbox"][contenteditable]',
                'div[contenteditable="true"]',
            ], text[:1000], "DM reply")
            if not filled:
                return {"success": False, "error": "DM input not found after opening conversation"}

            # Envoyer: essayer Enter, puis le bouton send
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1000)

            # Verifier si le message est parti (le champ devrait se vider)
            # Fallback: cliquer sur le bouton Send si Enter n'a pas marche
            try:
                send_btn = page.locator('[data-testid="dmComposerSendButton"], [aria-label="Send" i], button[type="submit"]').first
                if await send_btn.is_visible(timeout=1000):
                    await send_btn.click()
                    await page.wait_for_timeout(1000)
            except Exception:
                pass

            self._record_action("dm", self._content_hash("dm_reply", f"{contact_name}:{text[:30]}"))
            return {"success": True, "contact": contact_name}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_telegram_messages(self, group_name: str, max_messages: int = 10) -> list:
        """Lit les derniers messages d'un groupe/chat Telegram."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto("https://web.telegram.org/a/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            # Chercher le groupe
            search = page.locator('#telegram-search-input, input[placeholder*="Search" i]').first
            if await search.is_visible(timeout=5000):
                await search.click()
                await search.fill(group_name)
                await page.wait_for_timeout(3000)
                found = False
                items = await page.locator('.ListItem').all()
                for item in items[:5]:
                    try:
                        text = await item.inner_text()
                        if group_name.lower() in text.lower():
                            await item.click()
                            found = True
                            break
                    except Exception:
                        continue
                if not found:
                    return []
                await page.wait_for_timeout(2000)

            # Lire les messages
            messages = []
            msg_els = await page.locator('.message .text-content, .Message .text-content').all()
            for msg in msg_els[-max_messages:]:
                try:
                    text = await msg.inner_text()
                    if text:
                        messages.append(text[:500])
                except Exception:
                    continue

            return messages

        except Exception as e:
            print(f"[BrowserAgent] Read Telegram error: {e}")
            return []

    async def read_discord_messages(self, channel_url: str, max_messages: int = 10) -> list:
        """Lit les derniers messages d'un channel Discord."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(channel_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

            messages = []
            msg_els = await page.locator('[id^="message-content-"], div[class*="messageContent"]').all()
            for msg in msg_els[-max_messages:]:
                try:
                    text = await msg.inner_text()
                    if text:
                        messages.append(text[:500])
                except Exception:
                    continue

            return messages

        except Exception as e:
            print(f"[BrowserAgent] Read Discord error: {e}")
            return []

    # ── Veille concurrentielle ──

    async def competitive_scan(self, urls: list) -> list:
        """Screenshot + extraction de plusieurs pages concurrentes."""
        results = []
        for url in urls[:5]:  # Max 5 par scan
            try:
                path = await self.screenshot_page(url)
                text = await self.browse_and_extract(url, "main")
                results.append({"url": url, "screenshot": path, "extract": text[:500]})
            except Exception as e:
                results.append({"url": url, "error": str(e)})
        return results


# Singleton
browser = BrowserAgent()
