"""CEO Local — Memoire semantique ChromaDB.

Le CEO peut chercher par sens, pas juste par hash/keyword.
Exemples :
  mem.store_action("reply", "@user1", "Great point about DeFi yields on Solana")
  mem.store_action("reply", "@user2", "Interesting take on AI agent infrastructure")
  mem.has_similar("reply", "DeFi yields discussion") → True (deja couvert)
  mem.search("what did we say about DeFi") → [results with scores]

Collections :
  actions   — tweets, replies, comments, DMs, posts (dedup semantique)
  decisions — choix strategiques du CEO
  contacts  — infos sur les users rencontres
  learnings — regles et apprentissages
"""
import os
import time
import json

_CHROMA_OK = False
try:
    import chromadb
    import chromadb.config
    _CHROMA_OK = True
except ImportError:
    pass

_DIR = os.path.join(os.path.dirname(__file__), "ceo_vector_db")


class LocalVectorMemory:

    def __init__(self, persist_dir: str = _DIR):
        self._client = None
        self._collections = {}
        self._ok = False
        if not _CHROMA_OK:
            print("[VectorMem] chromadb non installe — memoire semantique desactivee")
            return
        try:
            os.makedirs(persist_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=persist_dir,
                settings=chromadb.config.Settings(anonymized_telemetry=False),
            )
            self._ok = True
            print(f"[VectorMem] ChromaDB OK ({persist_dir})")
        except Exception as e:
            print(f"[VectorMem] Init failed: {e}")

    def _coll(self, name: str):
        if not self._ok:
            return None
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    # ── Store ──

    def store(self, collection: str, text: str, metadata: dict | None = None):
        """Stocke un texte avec embedding semantique."""
        if not self._ok or not text or len(text.strip()) < 5:
            return
        meta = {k: (v if isinstance(v, (str, int, float, bool)) else str(v))
                for k, v in (metadata or {}).items()}
        meta.setdefault("ts", int(time.time()))
        meta.setdefault("date", time.strftime("%Y-%m-%d %H:%M"))
        doc_id = f"{collection}_{int(time.time()*1000)}_{hash(text) % 10000}"
        try:
            self._coll(collection).add(documents=[text], metadatas=[meta], ids=[doc_id])
        except Exception as e:
            print(f"[VectorMem] Store error: {e}")

    def store_action(self, action_type: str, target: str, content: str, platform: str = ""):
        """Stocke une action (tweet, reply, comment, DM, post, etc.)."""
        text = f"[{action_type}] {target}: {content}"
        self.store("actions", text, {
            "action": action_type,
            "target": target,
            "platform": platform or self._guess_platform(action_type),
        })

    def store_decision(self, summary: str, cycle: int = 0):
        self.store("decisions", summary, {"cycle": cycle})

    def store_contact(self, username: str, platform: str, info: str):
        self.store("contacts", f"@{username} ({platform}): {info}", {
            "username": username, "platform": platform,
        })

    def store_learning(self, rule: str, source: str = ""):
        self.store("learnings", rule, {"source": source})

    # ── Search ──

    def search(self, query: str, collection: str | None = None, n: int = 5) -> list:
        """Recherche semantique. Retourne [{text, score, metadata}]."""
        if not self._ok:
            return []
        colls = [collection] if collection else ["actions", "decisions", "contacts", "learnings"]
        results = []
        for name in colls:
            try:
                c = self._coll(name)
                if c.count() == 0:
                    continue
                r = c.query(query_texts=[query], n_results=min(n, c.count()))
                if r and r["documents"] and r["documents"][0]:
                    for i, doc in enumerate(r["documents"][0]):
                        dist = r["distances"][0][i] if r["distances"] and r["distances"][0] else 0
                        meta = r["metadatas"][0][i] if r["metadatas"] and r["metadatas"][0] else {}
                        results.append({"text": doc, "score": round(1 - dist, 3),
                                        "collection": name, "metadata": meta})
            except Exception:
                pass
        results.sort(key=lambda x: -x["score"])
        return results[:n]

    def has_similar(self, action_type: str, content: str, threshold: float = 0.85) -> bool:
        """Verifie si une action similaire existe deja (dedup semantique)."""
        if not self._ok:
            return False
        results = self.search(f"[{action_type}] {content}", collection="actions", n=3)
        for r in results:
            if r["score"] >= threshold:
                # Verifier que c'est le meme type d'action
                if r.get("metadata", {}).get("action") == action_type:
                    return True
        return False

    def search_context(self, query: str, n: int = 5) -> str:
        """Retourne les resultats formates pour injection dans un prompt LLM."""
        results = self.search(query, n=n)
        if not results:
            return "(Aucun souvenir pertinent)"
        lines = []
        for r in results:
            pct = int(r["score"] * 100)
            date = r.get("metadata", {}).get("date", "")
            lines.append(f"[{pct}% | {r['collection']} | {date}] {r['text'][:200]}")
        return "\n".join(lines)

    # ── Stats ──

    def stats(self) -> dict:
        if not self._ok:
            return {"backend": "disabled", "total": 0}
        counts = {}
        for name in ["actions", "decisions", "contacts", "learnings"]:
            try:
                counts[name] = self._coll(name).count()
            except Exception:
                counts[name] = 0
        return {"backend": "chromadb", "collections": counts, "total": sum(counts.values())}

    # ── Utils ──

    @staticmethod
    def _guess_platform(action_type: str) -> str:
        mapping = {
            "tweet": "twitter", "reply": "twitter", "like": "twitter",
            "follow": "twitter", "dm": "twitter",
            "reddit_post": "reddit", "reddit_comment": "reddit", "reddit_upvote": "reddit",
            "github_comment": "github", "star": "github",
            "discord_msg": "discord", "telegram_msg": "telegram", "email": "email",
        }
        return mapping.get(action_type, "unknown")


# Singleton
vmem = LocalVectorMemory()
