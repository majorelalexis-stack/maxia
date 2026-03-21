"""Browser Agent — browser-use (DOM + vision) pour poster sur X, Reddit, etc.

Remplace Playwright brut par browser-use qui utilise le DOM + vision via LLM.
Plus de selectors CSS fragiles — l'IA trouve les elements et s'adapte aux changements d'UI.
"""
import asyncio
import os
import time
from config_local import (
    BROWSER_PROFILE_DIR, MAX_TWEETS_DAY, MAX_REDDIT_POSTS_DAY,
    OLLAMA_URL, OLLAMA_VISION_MODEL,
)


class BrowserAgent:
    """Controle un navigateur via browser-use (LLM-driven, robuste aux changements d'UI)."""

    def __init__(self):
        self._agent = None
        self._browser = None
        self._initialized = False
        self._daily_counts = {"tweets": 0, "reddit": 0, "date": ""}
        self._profile_dir = BROWSER_PROFILE_DIR

    def _reset_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if self._daily_counts["date"] != today:
            self._daily_counts = {"tweets": 0, "reddit": 0, "date": today}

    def _get_llm(self):
        """Retourne le LLM pour browser-use (Ollama local, 0 cout)."""
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=OLLAMA_VISION_MODEL,
            base_url=OLLAMA_URL,
            temperature=0.3,
        )

    async def setup(self):
        """Initialise browser-use avec profil persistant."""
        if self._initialized:
            return
        try:
            from browser_use import Browser, BrowserConfig
            os.makedirs(self._profile_dir, exist_ok=True)
            config = BrowserConfig(
                headless=False,
                chrome_instance_path=None,
                extra_chromium_args=[
                    f"--user-data-dir={self._profile_dir}",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            self._browser = Browser(config=config)
            self._initialized = True
            print("[BrowserAgent] browser-use initialise avec profil persistant")
        except Exception as e:
            print(f"[BrowserAgent] Setup failed: {e}")
            raise

    async def close(self):
        """Ferme le navigateur."""
        if self._browser:
            await self._browser.close()
        self._initialized = False

    async def _run_task(self, task: str, max_actions: int = 10) -> str:
        """Execute une tache en langage naturel via browser-use."""
        if not self._initialized:
            await self.setup()
        from browser_use import Agent
        agent = Agent(
            task=task,
            llm=self._get_llm(),
            browser=self._browser,
            max_actions_per_step=max_actions,
        )
        result = await agent.run()
        return result.final_result() if result else ""

    async def post_tweet(self, text: str, media: str = None) -> dict:
        """Poste un tweet sur X via browser-use."""
        self._reset_if_new_day()
        if self._daily_counts["tweets"] >= MAX_TWEETS_DAY:
            return {"success": False, "error": f"Limite tweets/jour atteinte ({MAX_TWEETS_DAY})"}

        try:
            task = f'Go to x.com. Click on the compose/new post button. Type exactly this text: "{text[:280]}". Then click the Post button to publish the tweet.'
            if media and os.path.exists(media):
                task += f" Before posting, upload the image at {media}."

            result = await self._run_task(task, max_actions=8)

            # Screenshot preuve
            proof_path = os.path.join(self._profile_dir, f"tweet_{int(time.time())}.png")
            try:
                context = await self._browser.get_current_context()
                page = context.pages[-1] if context.pages else None
                if page:
                    await page.screenshot(path=proof_path)
            except Exception:
                proof_path = ""

            self._daily_counts["tweets"] += 1
            return {"success": True, "proof": proof_path, "text": text[:100], "result": str(result)[:200]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def reply_tweet(self, tweet_url: str, text: str) -> dict:
        """Repond a un tweet specifique."""
        try:
            task = f'Go to {tweet_url}. Click on the reply field. Type exactly: "{text[:280]}". Click the Reply button to post.'
            result = await self._run_task(task, max_actions=8)
            return {"success": True, "url": tweet_url, "reply": text[:100], "result": str(result)[:200]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def post_reddit(self, subreddit: str, title: str, body: str) -> dict:
        """Poste sur un subreddit."""
        self._reset_if_new_day()
        if self._daily_counts["reddit"] >= MAX_REDDIT_POSTS_DAY:
            return {"success": False, "error": f"Limite reddit/jour atteinte ({MAX_REDDIT_POSTS_DAY})"}

        try:
            task = (
                f'Go to reddit.com/r/{subreddit}. '
                f'Click on "Create Post" or the new post button. '
                f'Set the title to: "{title[:300]}". '
                f'In the body/text area, type: "{body[:2000]}". '
                f'Click the Post/Submit button.'
            )
            result = await self._run_task(task, max_actions=10)

            proof_path = os.path.join(self._profile_dir, f"reddit_{int(time.time())}.png")
            try:
                context = await self._browser.get_current_context()
                page = context.pages[-1] if context.pages else None
                if page:
                    await page.screenshot(path=proof_path)
            except Exception:
                proof_path = ""

            self._daily_counts["reddit"] += 1
            return {"success": True, "proof": proof_path, "subreddit": subreddit, "result": str(result)[:200]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def search_google(self, query: str, max_results: int = 10) -> list:
        """Recherche Google et extrait les resultats."""
        try:
            task = (
                f'Go to google.com. Search for: "{query}". '
                f'Extract the titles and URLs of the first {max_results} search results. '
                f'Return them as a list.'
            )
            result = await self._run_task(task, max_actions=5)
            # browser-use retourne du texte, on le parse basiquement
            return [{"title": line, "url": ""} for line in str(result).split("\n") if line.strip()][:max_results]
        except Exception as e:
            print(f"[BrowserAgent] Google search error: {e}")
            return []

    async def screenshot_page(self, url: str) -> str:
        """Capture une page (veille concurrentielle)."""
        if not self._initialized:
            await self.setup()
        try:
            task = f"Go to {url} and wait for the page to fully load."
            await self._run_task(task, max_actions=3)

            path = os.path.join(self._profile_dir, f"screenshot_{int(time.time())}.png")
            context = await self._browser.get_current_context()
            page = context.pages[-1] if context.pages else None
            if page:
                await page.screenshot(path=path, full_page=True)
                return path
            return ""
        except Exception as e:
            print(f"[BrowserAgent] Screenshot error: {e}")
            return ""

    async def browse_and_extract(self, url: str, what: str = "the main content") -> str:
        """Navigue vers une URL et extrait du contenu (langage naturel)."""
        try:
            task = f"Go to {url}. Extract {what} from the page. Return the text content."
            result = await self._run_task(task, max_actions=5)
            return str(result)[:5000]
        except Exception as e:
            return f"Error: {e}"


# Singleton
browser = BrowserAgent()
