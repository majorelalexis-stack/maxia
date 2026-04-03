"""CEO MAXIA — Vector Memory (ChromaDB)

Real semantic search. The CEO can find past decisions, conversations,
and strategies by meaning, not just keywords.

ChromaDB runs locally, no API key needed, no external service.
Falls back to keyword RAG if ChromaDB not installed.

Usage:
  from agents.ceo_vector_memory import vector_memory
  vector_memory.store("decisions", "Changed HUNTER from memo to Reddit because 0% whale conversion")
  results = vector_memory.search("whale conversion problem")
  → [{"text": "Changed HUNTER from memo to Reddit...", "score": 0.92, "metadata": {...}}]
"""
import logging
import json, os, time
from datetime import datetime

logger = logging.getLogger(__name__)

_CHROMA_AVAILABLE = False
try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    pass

# Fallback to keyword search if ChromaDB not available
from agents.ceo_rag import RAGLocal, rag as keyword_rag


class VectorMemory:
    """Semantic vector memory using ChromaDB."""

    def __init__(self, persist_dir: str = "ceo_vector_db"):
        self._persist_dir = persist_dir
        self._client = None
        self._collections = {}
        self._fallback = keyword_rag
        self._initialized = False

        if _CHROMA_AVAILABLE:
            try:
                os.makedirs(persist_dir, exist_ok=True)
                self._client = chromadb.PersistentClient(path=persist_dir)
                self._initialized = True
                logger.info(f"[VectorMemory] ChromaDB initialized at {persist_dir}")
            except Exception as e:
                logger.error(f"[VectorMemory] ChromaDB init failed: {e}, using keyword fallback")
        else:
            logger.warning("[VectorMemory] ChromaDB not installed, using keyword fallback")

    def _get_collection(self, name: str):
        """Get or create a ChromaDB collection."""
        if not self._initialized:
            return None
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def store(self, collection: str, text: str, metadata: dict = None):
        """Store a text with semantic embedding."""
        if not text or len(text.strip()) < 5:
            return

        meta = metadata or {}
        meta["timestamp"] = meta.get("timestamp", int(time.time()))
        meta["date"] = meta.get("date", datetime.now().strftime("%Y-%m-%d %H:%M"))
        # ChromaDB metadata values must be str, int, float, or bool
        clean_meta = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            else:
                clean_meta[k] = str(v)

        doc_id = f"{collection}_{int(time.time()*1000)}_{hash(text) % 10000}"

        if self._initialized:
            try:
                coll = self._get_collection(collection)
                coll.add(
                    documents=[text],
                    metadatas=[clean_meta],
                    ids=[doc_id],
                )
            except Exception as e:
                logger.error(f"[VectorMemory] Store error: {e}")

        # Also store in keyword fallback
        try:
            self._fallback.index_entry(collection, doc_id, text)
        except Exception:
            pass

    def search(self, query: str, collection: str = None, max_results: int = 5) -> list:
        """Semantic search across memory."""
        results = []

        if self._initialized:
            collections_to_search = [collection] if collection else ["decisions", "conversations", "strategies", "errors", "general"]
            for coll_name in collections_to_search:
                try:
                    coll = self._get_collection(coll_name)
                    if coll.count() == 0:
                        continue
                    r = coll.query(
                        query_texts=[query],
                        n_results=min(max_results, coll.count()),
                    )
                    if r and r["documents"] and r["documents"][0]:
                        for i, doc in enumerate(r["documents"][0]):
                            score = 1 - (r["distances"][0][i] if r["distances"] and r["distances"][0] else 0)
                            meta = r["metadatas"][0][i] if r["metadatas"] and r["metadatas"][0] else {}
                            results.append({
                                "text": doc,
                                "score": round(score, 3),
                                "collection": coll_name,
                                "metadata": meta,
                            })
                except Exception as e:
                    logger.error(f"[VectorMemory] Search error in {coll_name}: {e}")

            # Sort by score
            results.sort(key=lambda x: -x["score"])
            return results[:max_results]

        # Fallback to keyword search
        try:
            kw_results = self._fallback.search(query, max_results)
            return [{"text": r["text"], "score": r.get("score", 0.5), "collection": r.get("collection", ""), "metadata": {}} for r in kw_results]
        except Exception:
            return []

    def search_context(self, query: str, max_results: int = 5) -> str:
        """Get search results as a formatted context string for LLM."""
        results = self.search(query, max_results=max_results)
        if not results:
            return "(Aucun souvenir pertinent)"
        lines = []
        for r in results:
            score_pct = int(r["score"] * 100)
            date = r.get("metadata", {}).get("date", "")
            lines.append(f"[{score_pct}% match | {r['collection']} | {date}] {r['text'][:200]}")
        return "\n".join(lines)

    def store_decision(self, decision: dict):
        """Store a CEO decision."""
        text = json.dumps(decision, ensure_ascii=False, default=str)
        summary = decision.get("summary", decision.get("action", text[:200]))
        self.store("decisions", summary, {
            "type": "decision",
            "cycle": str(decision.get("cycle", "")),
            "agent": str(decision.get("agent", "")),
        })

    def store_conversation(self, conv: dict):
        """Store a conversation."""
        text = f"{conv.get('canal','')}: {conv.get('user','')}: {conv.get('message','')} → {conv.get('reponse','')}"
        self.store("conversations", text, {
            "type": "conversation",
            "canal": str(conv.get("canal", "")),
            "user": str(conv.get("user", "")),
            "intention": str(conv.get("intention", "")),
        })

    def store_error(self, source: str, error: str):
        """Store an error for pattern detection."""
        self.store("errors", f"{source}: {error}", {
            "type": "error",
            "source": source,
        })

    def store_strategy(self, strategy: dict):
        """Store a strategic insight."""
        text = json.dumps(strategy, ensure_ascii=False, default=str)
        self.store("strategies", text[:500], {
            "type": "strategy",
        })

    def stats(self) -> dict:
        """Get memory statistics."""
        if self._initialized:
            collections = {}
            for name in ["decisions", "conversations", "strategies", "errors", "general"]:
                try:
                    coll = self._get_collection(name)
                    collections[name] = coll.count()
                except Exception:
                    collections[name] = 0
            return {
                "backend": "chromadb",
                "initialized": True,
                "collections": collections,
                "total_entries": sum(collections.values()),
            }
        return {
            "backend": "keyword_fallback",
            "initialized": False,
            "stats": self._fallback.stats(),
        }


# Global instance
vector_memory = VectorMemory()
