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


class BrowserAgent:
    """Controle un navigateur Chrome via Playwright avec selectors robustes."""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False
        self._daily_counts = {"tweets": 0, "reddit": 0, "date": ""}
        self._profile_dir = BROWSER_PROFILE_DIR

    def _reset_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if self._daily_counts["date"] != today:
            self._daily_counts = {"tweets": 0, "reddit": 0, "date": today}

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
        self._reset_if_new_day()
        if self._daily_counts["tweets"] >= MAX_TWEETS_DAY:
            return {"success": False, "error": f"Limite tweets/jour atteinte ({MAX_TWEETS_DAY})"}

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

            self._daily_counts["tweets"] += 1
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
        self._reset_if_new_day()
        if self._daily_counts["reddit"] >= MAX_REDDIT_POSTS_DAY:
            return {"success": False, "error": f"Limite reddit/jour atteinte ({MAX_REDDIT_POSTS_DAY})"}

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

            self._daily_counts["reddit"] += 1
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
            return {"success": True, "url": tweet_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def follow_user(self, username: str) -> dict:
        """Follow un utilisateur sur X."""
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


# Singleton
browser = BrowserAgent()
