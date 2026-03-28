"""CEO MAXIA — RAG Local (Memoire Associative)

Index de recherche par mots-cles. Pas de base vectorielle.
Fonctionne avec des fichiers JSON sur un NAS.

Usage:
  rag = RAGLocal()
  rag.index_entry("decisions", 47, "Changement canal HUNTER memo vers reddit car 0% conversion whale")
  results = rag.search("whale conversion")
  → [{"collection": "decisions", "id": 47, "text": "...", "score": 2}]

L'archive complete est gardee dans des fichiers separes.
Le contexte LLM ne charge que les resultats de recherche pertinents.
"""
import json, logging, os, re
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


class RAGLocal:
    """Index de recherche local par mots-cles."""

    def __init__(self, data_dir="ceo_archives"):
        self._dir = data_dir
        self._index_path = os.path.join(data_dir, "index.json")
        self._index: dict = {}  # mot -> [(collection, id, score)]
        self._archives: dict = {}  # collection -> {id -> text}
        os.makedirs(data_dir, exist_ok=True)
        self._load_index()

    def _load_index(self):
        try:
            if os.path.exists(self._index_path):
                with open(self._index_path) as f:
                    self._index = json.load(f)
        except Exception:
            self._index = {}

    def _save_index(self):
        try:
            with open(self._index_path, "w") as f:
                json.dump(self._index, f, default=str)
        except Exception as e:
            logger.error("Save error: %s", e)

    def _get_archive_path(self, collection: str) -> str:
        return os.path.join(self._dir, f"{collection}.json")

    def _load_archive(self, collection: str) -> dict:
        if collection in self._archives:
            return self._archives[collection]
        path = self._get_archive_path(collection)
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                    self._archives[collection] = data
                    return data
        except Exception:
            pass
        self._archives[collection] = {}
        return self._archives[collection]

    def _save_archive(self, collection: str):
        try:
            path = self._get_archive_path(collection)
            with open(path, "w") as f:
                json.dump(self._archives.get(collection, {}), f, indent=1, default=str)
        except Exception as e:
            logger.error("Archive save error: %s", e)

    def _tokenize(self, text: str) -> list:
        """Extrait les mots-cles significatifs (>3 chars, lowercase)."""
        words = re.findall(r'[a-zA-Z0-9]+', text.lower())
        # Filtrer les mots trop courts et les stop words
        stop = {"the", "and", "for", "that", "this", "with", "from", "have", "been",
                "will", "are", "was", "were", "has", "had", "not", "but", "all",
                "can", "her", "his", "our", "they", "them", "than", "then", "also",
                "dans", "pour", "avec", "les", "des", "une", "par", "sur", "est",
                "pas", "plus", "que", "qui", "aux", "son", "ses"}
        return [w for w in words if len(w) > 2 and w not in stop]

    # ── Indexation ──

    def index_entry(self, collection: str, entry_id, text: str):
        """Indexe une entree dans l'archive + l'index de mots-cles."""
        str_id = str(entry_id)

        # Archiver le texte complet
        archive = self._load_archive(collection)
        archive[str_id] = {
            "text": text[:2000],
            "ts": datetime.utcnow().isoformat(),
        }
        self._save_archive(collection)

        # Indexer les mots-cles
        tokens = self._tokenize(text)
        for token in set(tokens):  # Unique tokens
            key = f"{token}"
            if key not in self._index:
                self._index[key] = []
            # Eviter les doublons
            ref = f"{collection}:{str_id}"
            if not any(r.get("ref") == ref for r in self._index[key]):
                self._index[key].append({
                    "ref": ref,
                    "collection": collection,
                    "id": str_id,
                })
            # Limiter a 100 refs par mot
            self._index[key] = self._index[key][-100:]

        self._save_index()

    def index_decision(self, decision: dict):
        """Raccourci pour indexer une decision."""
        text = f"{decision.get('decision', '')} {decision.get('raison', '')} {decision.get('cible', '')}"
        entry_id = f"d_{len(self._load_archive('decisions'))}"
        self.index_entry("decisions", entry_id, text)

    def index_conversation(self, conv: dict):
        """Raccourci pour indexer une conversation."""
        text = f"{conv.get('canal', '')} {conv.get('user', '')} {conv.get('msg', '')} {conv.get('rep', '')} {conv.get('intention', '')}"
        entry_id = f"c_{len(self._load_archive('conversations'))}"
        self.index_entry("conversations", entry_id, text)

    def index_strategy(self, strat: dict):
        """Raccourci pour indexer une strategie."""
        text = json.dumps(strat, default=str)[:1000]
        entry_id = f"s_{len(self._load_archive('strategies'))}"
        self.index_entry("strategies", entry_id, text)

    # ── Recherche ──

    def search(self, query: str, max_results: int = 5) -> list:
        """Cherche dans l'index et retourne les entrees les plus pertinentes."""
        tokens = self._tokenize(query)
        if not tokens:
            return []

        # Compter les hits par ref
        scores: dict = defaultdict(int)
        refs: dict = {}

        for token in tokens:
            entries = self._index.get(token, [])
            for entry in entries:
                ref = entry["ref"]
                scores[ref] += 1
                refs[ref] = entry

        # Trier par score
        sorted_refs = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:max_results]

        # Recuperer les textes complets
        results = []
        for ref, score in sorted_refs:
            entry = refs[ref]
            collection = entry["collection"]
            entry_id = entry["id"]
            archive = self._load_archive(collection)
            archived = archive.get(entry_id, {})
            results.append({
                "collection": collection,
                "id": entry_id,
                "text": archived.get("text", ""),
                "ts": archived.get("ts", ""),
                "score": score,
                "matched_tokens": score,
            })

        return results

    def search_context(self, query: str, max_results: int = 5) -> str:
        """Retourne les resultats formates pour le contexte LLM."""
        results = self.search(query, max_results)
        if not results:
            return "(Aucun resultat dans les archives)"

        lines = [f"ARCHIVES — Recherche: '{query}' ({len(results)} resultats)"]
        for r in results:
            lines.append(f"  [{r['collection']}] (score:{r['score']}) {r['text'][:200]}")
        return "\n".join(lines)

    # ── Stats ──

    def stats(self) -> dict:
        collections = {}
        for f in os.listdir(self._dir):
            if f.endswith(".json") and f != "index.json":
                name = f.replace(".json", "")
                archive = self._load_archive(name)
                collections[name] = len(archive)
        return {
            "index_size": len(self._index),
            "collections": collections,
            "total_entries": sum(collections.values()),
        }


# Singleton
rag = RAGLocal()


if __name__ == "__main__":
    # Test
    print("=== RAG Local Test ===")

    rag.index_entry("decisions", "d1", "Changement canal HUNTER de memo vers reddit car 0% conversion whale")
    rag.index_entry("decisions", "d2", "Commission crypto: BRONZE 0.10%, SILVER 0.05%, GOLD 0.03%, WHALE 0.01%")
    rag.index_entry("decisions", "d3", "Tweet publie sur les AI tokens trending RENDER PYTH")
    rag.index_entry("conversations", "c1", "twitter_dm dev_42 How do I swap SOL to USDC question_technique")
    rag.index_entry("conversations", "c2", "discord whale_99 Is this project legit prospect")
    rag.index_entry("conversations", "c3", "telegram angry My swap failed plainte")
    rag.index_entry("strategies", "s1", "Reddit convertit mieux que Twitter. Cibler les developpeurs.")

    print("\nSearch 'whale conversion':")
    for r in rag.search("whale conversion"):
        print(f"  [{r['collection']}] score={r['score']}: {r['text'][:80]}")

    print("\nSearch 'swap reddit':")
    for r in rag.search("swap reddit"):
        print(f"  [{r['collection']}] score={r['score']}: {r['text'][:80]}")

    print("\nSearch 'AI tokens':")
    for r in rag.search("AI tokens"):
        print(f"  [{r['collection']}] score={r['score']}: {r['text'][:80]}")

    print(f"\nContext for LLM:\n{rag.search_context('whale repondu conversion')}")

    print(f"\nStats: {rag.stats()}")
