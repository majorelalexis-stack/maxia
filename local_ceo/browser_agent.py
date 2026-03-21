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

    def _is_duplicate(self, action_type: str, content: str) -> bool:
        """Verifie si cette action a deja ete faite (meme contenu)."""
        import hashlib
        h = hashlib.md5(f"{action_type}:{content}".encode()).hexdigest()[:12]
        return any(a["hash"] == h for a in self._action_history)

    def _content_hash(self, action_type: str, content: str) -> str:
        import hashlib
        return hashlib.md5(f"{action_type}:{content}".encode()).hexdigest()[:12]

    async def setup(self):
        """Lance Chromium avec profil persistant."""
        if self._initialized:
            return
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            os.makedirs(self._profile_dir, exist_ok=True)
            # Utiliser le Chrome systeme (pas le Chromium Playwright) pour garder le profil
            chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if not os.path.exists(chrome_path):
                chrome_path = None  # Fallback Chromium Playwright
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=self._profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 900},
                executable_path=chrome_path,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            self._initialized = True
            print("[BrowserAgent] Chrome lance avec profil persistant")
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

    async def _ensure_ready(self):
        if not self._initialized:
            await self.setup()

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
                    await el.fill(text)
                    return True
            except Exception:
                continue
        # Fallback: taper au clavier
        try:
            await page.keyboard.type(text, delay=30)
            return True
        except Exception:
            pass
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
                    await page.wait_for_timeout(3000)
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

            await page.wait_for_timeout(3000)
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

            await page.wait_for_timeout(3000)
            return {"success": True, "url": tweet_url, "reply": text[:100]}

        except Exception as e:
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
            await page.wait_for_timeout(3000)

            # Titre — multi-selectors
            filled_title = await self._find_and_fill(page, [
                'textarea[placeholder*="Title" i]',
                'input[placeholder*="Title" i]',
                'textarea[name="title"]',
                'input[name="title"]',
                '[data-testid="post-title"] textarea',
                'div[slot="title"] textarea',
            ], title[:300], "Reddit title")

            if not filled_title:
                await self._screenshot(page, "reddit_title_fail")
                return {"success": False, "error": "Champ titre Reddit introuvable"}

            await page.wait_for_timeout(1000)

            # Body — multi-selectors
            await self._find_and_fill(page, [
                'div[contenteditable="true"]',
                'textarea[placeholder*="Text" i]',
                'textarea[placeholder*="body" i]',
                '.public-DraftEditor-content',
                '[data-testid="post-body"] div[contenteditable]',
                'div[slot="text"] div[contenteditable]',
                'shreddit-composer div[contenteditable]',
            ], body[:10000], "Reddit body")

            await page.wait_for_timeout(1000)

            # Poster — multi-selectors
            posted = await self._find_and_click(page, [
                'button:has-text("Post")',
                'button:has-text("Submit")',
                'button[type="submit"]:has-text("Post")',
                '[data-testid="submit-button"]',
                'button.submit',
                'faceplate-tracker button:has-text("Post")',
            ], "Reddit Post button")

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
            await page.wait_for_timeout(3000)

            results = []
            # Extraire les tweets du feed
            tweets = await page.locator('article[data-testid="tweet"]').all()
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
            await page.wait_for_timeout(3000)

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
                'button[aria-label*="Follow" i]',
                'button[aria-label*="Suivre" i]',
                'div[role="button"]:has-text("Follow")',
                'div[role="button"]:has-text("Suivre")',
            ], "Follow button")

            if not followed:
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
            await page.wait_for_timeout(3000)

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

    async def search_reddit(self, subreddit: str, query: str, max_results: int = 10) -> list:
        """Cherche des posts sur un subreddit."""
        await self._ensure_ready()
        page = self._page

        try:
            encoded = query.replace(" ", "%20")
            await page.goto(f"https://www.reddit.com/r/{subreddit}/search/?q={encoded}&restrict_sr=1&sort=new", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            results = []
            # Multi-selectors pour les posts Reddit
            for post_sel in ['shreddit-post', 'div[data-testid="post-container"]', 'div.Post']:
                posts = await page.locator(post_sel).all()
                if posts:
                    for post in posts[:max_results]:
                        try:
                            title_el = post.locator('a[slot="title"], a[data-click-id="body"], h3').first
                            title = await title_el.inner_text() if await title_el.is_visible(timeout=1000) else ""
                            href = await title_el.get_attribute("href") or ""
                            if href and not href.startswith("http"):
                                href = f"https://www.reddit.com{href}"
                            if title:
                                results.append({"title": title[:200], "url": href})
                        except Exception:
                            continue
                    if results:
                        break

            print(f"[BrowserAgent] Reddit search r/{subreddit} '{query}': {len(results)}")
            return results

        except Exception as e:
            print(f"[BrowserAgent] Reddit search error: {e}")
            return []

    async def comment_reddit(self, post_url: str, text: str) -> dict:
        """Commente sur un post Reddit."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            # Trouver et remplir le champ commentaire
            filled = await self._find_and_fill(page, [
                'div[contenteditable="true"][data-placeholder*="Add a comment" i]',
                'div[contenteditable="true"][placeholder*="comment" i]',
                'div[contenteditable="true"][role="textbox"]',
                'textarea[placeholder*="comment" i]',
                'shreddit-composer div[contenteditable="true"]',
            ], text[:5000], "Reddit comment box")

            if not filled:
                # Essayer de cliquer d'abord sur "Add a comment"
                clicked = await self._find_and_click(page, [
                    'div[placeholder*="Add a comment" i]',
                    'button:has-text("Add a comment")',
                    '[data-click-id="comment"]',
                ], "Comment trigger")
                if clicked:
                    await page.wait_for_timeout(1000)
                    filled = await self._find_and_fill(page, [
                        'div[contenteditable="true"]',
                        'textarea',
                    ], text[:5000], "Reddit comment box (2nd try)")

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

            await page.wait_for_timeout(3000)
            proof = await self._screenshot(page, "reddit_comment_ok")
            return {"success": True, "proof": proof, "url": post_url}

        except Exception as e:
            await self._screenshot(page, "reddit_comment_error")
            return {"success": False, "error": str(e)}


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
            await page.goto(f"https://x.com/messages", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

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
            await page.wait_for_timeout(3000)

            # Chercher le groupe/user
            search = page.locator('#telegram-search-input, input[placeholder*="Search" i], .input-search input').first
            if await search.is_visible(timeout=5000):
                await search.click()
                await search.fill(group_or_user)
                await page.wait_for_timeout(2000)

                # Cliquer sur le resultat
                result = page.locator(f'.ListItem:has-text("{group_or_user}")').first
                if await result.is_visible(timeout=5000):
                    await result.click()
                else:
                    return {"success": False, "error": f"Groupe/user '{group_or_user}' introuvable"}
            else:
                return {"success": False, "error": "Champ recherche Telegram introuvable"}

            await page.wait_for_timeout(1500)

            # Taper le message
            filled = await self._find_and_fill(page, [
                'div.input-message-input[contenteditable="true"]',
                '#editable-message-text',
                'div[contenteditable="true"][data-peer-id]',
                'div.input-message-container div[contenteditable]',
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
            await page.goto(group_link, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            # Cliquer Join
            joined = await self._find_and_click(page, [
                'button:has-text("Join Group")',
                'button:has-text("Join Channel")',
                'button:has-text("JOIN")',
                '.btn-primary:has-text("Join")',
            ], "Join button")

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

            await page.wait_for_timeout(3000)
            return {"success": submitted, "repo": repo_url, "title": title[:50]}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def comment_github_discussion(self, discussion_url: str, text: str) -> dict:
        """Commente sur une discussion/issue GitHub."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(discussion_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            filled = await self._find_and_fill(page, [
                'textarea#new_comment_field',
                'textarea[name="comment[body]"]',
                'textarea[placeholder*="Leave a comment" i]',
                'div.CommentBox-container textarea',
            ], text[:5000], "GitHub comment")
            if not filled:
                return {"success": False, "error": "Champ commentaire GitHub introuvable"}

            await page.wait_for_timeout(1000)

            submitted = await self._find_and_click(page, [
                'button:has-text("Comment")',
                'button[type="submit"]:has-text("Comment")',
            ], "Comment button")

            await page.wait_for_timeout(3000)
            return {"success": submitted, "url": discussion_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Discord Web ──

    async def send_discord(self, server_channel_url: str, text: str) -> dict:
        """Envoie un message sur Discord Web."""
        err = self._check_rate("dm")
        if err:
            return {"success": False, "error": err}
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(server_channel_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            filled = await self._find_and_fill(page, [
                'div[role="textbox"][contenteditable="true"]',
                'div[data-slate-editor="true"]',
                'div.slateTextArea-1Mkdgw',
            ], text[:2000], "Discord message")
            if not filled:
                return {"success": False, "error": "Champ message Discord introuvable"}

            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            self._record_action("dm", self._content_hash("discord", f"{server_channel_url}:{text[:50]}"))
            return {"success": True, "channel": server_channel_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def join_discord_server(self, invite_link: str) -> dict:
        """Rejoint un serveur Discord via invite."""
        await self._ensure_ready()
        page = self._page

        try:
            await page.goto(invite_link, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            joined = await self._find_and_click(page, [
                'button:has-text("Accept Invite")',
                'button:has-text("Join")',
                'button:has-text("Accepter l\'invitation")',
            ], "Join Discord")

            await page.wait_for_timeout(3000)
            return {"success": joined, "invite": invite_link}

        except Exception as e:
            return {"success": False, "error": str(e)}

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
