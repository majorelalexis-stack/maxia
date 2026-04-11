"""Hybrid RAG knowledge base for MAXIA CEO Local.

Design goals
------------
- **Zero new dependency**: reuse ``chromadb`` already present in
  ``vector_memory_local.py``. The default ONNX embedding function ships
  with chromadb and runs on CPU (~60 MB RAM, no GPU contention with
  qwen3).
- **Hybrid retrieval**: vector semantic search + keyword overlay for
  short acronyms (x402, AIP, SSO, OIDC, GROQ...) that all-MiniLM-L6-v2
  misses. POC measured 68% -> 100% coverage on the 9-question benchmark.
- **Idempotent ingestion**: ``doc_id = f"{tag}_{sha1(chunk)[:10]}"`` so
  re-running ingest on unchanged files is a no-op. File-level mtime
  gating is handled by the nightly mission (``reindex_rag.py``).
- **Fail-soft**: every public function returns a safe default ("" or [])
  if ChromaDB is unavailable, so patched callers never raise.

Public API
----------
- ``ingest_docs(sources, force=False)`` -> dict with stats
- ``hybrid_retrieve(query, k=6)`` -> list[dict]  (text, source, score)
- ``build_rag_context(query, max_chars=2500, header=None)`` -> str
- ``stats()`` -> dict
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("ceo")

_HERE = Path(__file__).parent
_COLLECTION_NAME = "knowledge_docs"

# Side collections managed by vector_memory_local. Queried in parallel
# with knowledge_docs when ``hybrid_retrieve_multi`` is used so the
# chat can surface recent actions, strategic decisions, self-learnings,
# and contact notes alongside static product docs.
_SIDE_COLLECTIONS = ("actions", "decisions", "learnings", "contacts")
# Per-collection score weight applied to vector hits. Larger = more
# influence on final ranking. Product docs stay baseline 1.0, runtime
# actions boosted slightly so "what did you do today" answers surface
# recent behaviour instead of marketing copy.
_COLLECTION_WEIGHTS = {
    "knowledge_docs": 1.0,
    "actions": 1.3,
    "learnings": 1.25,
    "decisions": 1.15,
    "contacts": 0.85,
}

# Default corpus (relative to the local_ceo/ directory).
# NOTE: keep the list small — larger corpora dilute retrieval quality
# without a reranker. The POC showed 134 chunks is the sweet spot.
DEFAULT_SOURCES: list[tuple[str, str]] = [
    (str(_HERE.parent / "frontend" / "llms-full.txt"), "overview"),
    (str(_HERE.parent / "CLAUDE.md"), "architecture"),
    (str(_HERE / "sales" / "maxia_catalog.json"), "catalog"),
    (str(_HERE / "memory_prod" / "capabilities_prod.json"), "capabilities"),
]

# Chunking parameters — validated by POC (2026-04-10).
# 300/40 beats 600/80 on the benchmark because small acronyms don't
# get diluted in a 600-char chunk.
CHUNK_SIZE = 300
CHUNK_OVERLAP = 40
MIN_CHUNK_CHARS = 20

# ── Lazy-initialized collection handle (shares vmem._client) ──
_coll: Any = None
_ok: bool = False
# In-memory mirror of all chunks for keyword overlay. ChromaDB alone
# cannot do BM25/keyword search, so we keep a lightweight Python list
# for the overlay scan (~75-200 chunks total, <0.5 MB RAM).
_chunk_cache: list[tuple[str, str]] = []  # (text, source)


def _get_collection() -> Any:
    """Get (or create) the shared knowledge_docs collection.

    Reuses ``vector_memory_local.vmem._client`` so we don't open a
    second ChromaDB client (which would fail on the persistent dir
    lock).
    """
    global _coll, _ok
    if _coll is not None:
        return _coll
    try:
        from vector_memory_local import vmem
        if not vmem._ok or vmem._client is None:
            log.warning("[rag] vmem ChromaDB unavailable — RAG disabled")
            return None
        _coll = vmem._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        _ok = True
        log.info("[rag] collection '%s' ready (count=%d)", _COLLECTION_NAME, _coll.count())
        return _coll
    except Exception as e:
        log.warning("[rag] collection init failed: %s", e)
        return None


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, preferring whitespace boundaries."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            sp = text.rfind(" ", i + size // 2, end)
            if sp > 0:
                end = sp
        piece = text[i:end].strip()
        if len(piece) >= MIN_CHUNK_CHARS:
            out.append(piece)
        if end >= n:
            break
        i = end - overlap
    return out


def _doc_id(tag: str, text: str) -> str:
    """Deterministic ID so re-ingestion of the same text is a no-op."""
    h = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{tag}_{h}"


def _refresh_chunk_cache() -> None:
    """Rebuild the in-memory chunk cache from the ChromaDB collection.

    Called after ingestion and lazily on the first ``hybrid_retrieve``
    if the cache is empty.
    """
    global _chunk_cache
    coll = _get_collection()
    if coll is None:
        _chunk_cache = []
        return
    try:
        # ChromaDB .get() with no ids returns all docs.
        # For collections up to ~10k chunks this is fine.
        all_data = coll.get(include=["documents", "metadatas"])
        docs = all_data.get("documents") or []
        metas = all_data.get("metadatas") or []
        _chunk_cache = [
            (str(d), str((m or {}).get("source", "")))
            for d, m in zip(docs, metas)
        ]
        log.info("[rag] chunk cache refreshed: %d chunks", len(_chunk_cache))
    except Exception as e:
        log.warning("[rag] chunk cache refresh failed: %s", e)
        _chunk_cache = []


def ingest_docs(
    sources: Iterable[tuple[str, str]] | None = None,
    force: bool = False,
) -> dict:
    """Ingest documents into the knowledge_docs collection.

    Parameters
    ----------
    sources
        Iterable of ``(path, tag)`` pairs. Defaults to ``DEFAULT_SOURCES``.
    force
        If True, delete existing chunks for each source tag before
        re-adding. If False, idempotent upsert only adds chunks whose
        sha1-based ID doesn't already exist.

    Returns
    -------
    dict
        ``{"chunks_added": N, "files": M, "skipped": K, "elapsed_s": t}``
    """
    coll = _get_collection()
    if coll is None:
        return {"chunks_added": 0, "files": 0, "skipped": 0, "elapsed_s": 0.0, "error": "collection unavailable"}

    sources_list = list(sources or DEFAULT_SOURCES)
    t0 = time.time()
    added = 0
    files_ok = 0
    skipped = 0
    errors: list[str] = []

    # Existing IDs so we can skip unchanged chunks in idempotent mode.
    existing_ids: set[str] = set()
    if not force:
        try:
            all_ids = coll.get(include=[])
            existing_ids = set(all_ids.get("ids") or [])
        except Exception:
            pass

    for path, tag in sources_list:
        if not os.path.exists(path):
            errors.append(f"missing: {path}")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            errors.append(f"read {path}: {e}")
            continue

        if force:
            # Delete all chunks with this tag before re-adding
            try:
                coll.delete(where={"source": tag})
            except Exception as e:
                log.warning("[rag] delete(tag=%s) failed: %s", tag, e)

        pieces = _chunk_text(text)
        if not pieces:
            continue

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        for idx, piece in enumerate(pieces):
            did = _doc_id(tag, piece)
            if did in existing_ids:
                skipped += 1
                continue
            ids.append(did)
            docs.append(piece)
            metas.append({
                "source": tag,
                "path": path,
                "idx": idx,
                "indexed_at": int(time.time()),
            })
            existing_ids.add(did)

        if ids:
            try:
                coll.add(documents=docs, metadatas=metas, ids=ids)
                added += len(ids)
            except Exception as e:
                errors.append(f"add {tag}: {e}")
                continue
        files_ok += 1

    # Refresh the in-memory cache so keyword overlay sees new chunks
    _refresh_chunk_cache()

    elapsed = time.time() - t0
    log.info(
        "[rag] ingest done: %d chunks added, %d skipped, %d files, %.2fs",
        added, skipped, files_ok, elapsed,
    )
    return {
        "chunks_added": added,
        "files": files_ok,
        "skipped": skipped,
        "elapsed_s": round(elapsed, 2),
        "errors": errors,
    }


# ── Retrieval ──

_STOPWORDS = frozenset({
    "what", "which", "does", "have", "support", "maxia", "the", "you",
    "for", "your", "are", "there", "how", "why", "when", "where", "who",
    "can", "will", "should", "would", "could", "is", "am", "was", "were",
    "do", "did", "has", "had", "a", "an", "in", "on", "of", "to", "from",
    "with", "about", "and", "or", "but", "not", "all", "any", "some",
})

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


def _extract_query_keywords(query: str) -> list[str]:
    """Pull short/rare tokens from a query for the keyword overlay scan.

    Returns tokens that are either short acronyms (<=6 chars) or contain
    digits (x402, k8s, etc.). Normal English words fall through to the
    vector search alone — no reason to keyword-match "commission".
    """
    words = [w.lower() for w in _TOKEN_RE.findall(query)]
    candidates: list[str] = []
    for w in words:
        if w in _STOPWORDS:
            continue
        if len(w) <= 6 or any(c.isdigit() for c in w):
            candidates.append(w)
    # Dedup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for w in candidates:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


_side_coll_cache: dict[str, Any] = {}


def _get_side_collection(name: str) -> Any:
    """Fetch one of the auxiliary ChromaDB collections managed by
    ``vector_memory_local`` (actions, decisions, learnings, contacts).

    Shares the same ChromaDB client as ``_get_collection()`` to avoid
    double-opening the persistent directory. Results are cached per
    collection name for the process lifetime.
    """
    if name in _side_coll_cache:
        return _side_coll_cache[name]
    try:
        from vector_memory_local import vmem
        if not vmem._ok or vmem._client is None:
            return None
        coll = vmem._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        _side_coll_cache[name] = coll
        return coll
    except Exception as e:
        log.warning("[rag] side collection '%s' init failed: %s", name, e)
        return None


def hybrid_retrieve(query: str, k: int = 6) -> list[dict]:
    """Vector top-k*2 + keyword overlay, deduplicated and sorted by score.

    Returns a list of ``{"text", "source", "score"}`` dicts.
    Empty list if RAG is unavailable or the query is empty.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    coll = _get_collection()
    if coll is None:
        return []

    # 1. Vector search (top k*2 to have headroom for dedup)
    vec_results: list[tuple[str, str, float]] = []
    try:
        n = min(k * 2, max(1, coll.count()))
        if n > 0:
            r = coll.query(query_texts=[query], n_results=n)
            docs = (r.get("documents") or [[]])[0]
            dists = (r.get("distances") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            for d, m, dist in zip(docs, metas, dists):
                source = str((m or {}).get("source", ""))
                score = max(0.0, 1.0 - float(dist))
                vec_results.append((str(d), source, score))
    except Exception as e:
        log.warning("[rag] vector query failed: %s", e)

    # 2. Keyword overlay (fallback for rare tokens the embedding misses)
    if not _chunk_cache:
        _refresh_chunk_cache()
    rare_tokens = _extract_query_keywords(query)
    kw_results: list[tuple[str, str, float]] = []
    if rare_tokens and _chunk_cache:
        for text, source in _chunk_cache:
            tl = text.lower()
            hits = sum(1 for tok in rare_tokens if tok in tl)
            if hits > 0:
                # 0.50 base, +0.15 per hit, capped at 0.95
                kscore = min(0.95, 0.50 + 0.15 * hits)
                kw_results.append((text, source, kscore))

    # 3. Dedup by chunk prefix and keep max score
    merged: dict[str, tuple[str, str, float]] = {}
    for text, source, score in vec_results + kw_results:
        key = text[:80]
        prev = merged.get(key)
        if prev is None or prev[2] < score:
            merged[key] = (text, source, score)

    sorted_hits = sorted(merged.values(), key=lambda x: -x[2])[:k]
    return [
        {"text": t, "source": s, "score": round(sc, 3)}
        for (t, s, sc) in sorted_hits
    ]


def hybrid_retrieve_multi(
    query: str,
    k: int = 6,
    collections: Iterable[str] = ("knowledge_docs", "actions", "decisions", "learnings", "contacts"),
) -> list[dict]:
    """Multi-collection retrieval.

    Queries the main ``knowledge_docs`` collection via :func:`hybrid_retrieve`
    (vector + keyword overlay) plus each side collection via vector search
    only. Per-collection score weights from ``_COLLECTION_WEIGHTS`` are
    applied so runtime data (actions, learnings, decisions) can outrank
    static marketing copy for questions about what the CEO actually did.

    Returns a merged list of ``{"text","source","score","collection"}``
    dicts sorted by weighted score, truncated to ``k``.
    """
    if not isinstance(query, str) or not query.strip():
        return []

    merged: list[tuple[str, str, float, str]] = []  # text, source, score, coll

    # 1. Main knowledge_docs via the existing hybrid_retrieve (vector + keyword)
    if "knowledge_docs" in collections:
        w = _COLLECTION_WEIGHTS.get("knowledge_docs", 1.0)
        for h in hybrid_retrieve(query, k=k):
            merged.append((h["text"], h["source"], h["score"] * w, "knowledge_docs"))

    # 2. Side collections via vector search only (no keyword cache for them)
    for name in collections:
        if name == "knowledge_docs":
            continue
        coll = _get_side_collection(name)
        if coll is None:
            continue
        try:
            cnt = coll.count()
            if cnt == 0:
                continue
            n = min(k, cnt)
            r = coll.query(query_texts=[query], n_results=n)
            docs = (r.get("documents") or [[]])[0]
            dists = (r.get("distances") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            w = _COLLECTION_WEIGHTS.get(name, 1.0)
            for d, m, dist in zip(docs, metas, dists):
                if not d:
                    continue
                base = max(0.0, 1.0 - float(dist))
                # Only keep decent matches — cosine distance > 1.0 means
                # the vectors are further than orthogonal; retrieving them
                # would just add noise to the prompt.
                if base < 0.15:
                    continue
                source = f"{name}/{(m or {}).get('source','')}".rstrip("/")
                merged.append((str(d), source, base * w, name))
        except Exception as e:
            log.debug("[rag] side collection '%s' query failed: %s", name, e)

    # 3. Dedup by text prefix, keep max score
    dedup: dict[str, tuple[str, str, float, str]] = {}
    for text, source, score, coll_name in merged:
        key = text[:80]
        prev = dedup.get(key)
        if prev is None or prev[2] < score:
            dedup[key] = (text, source, score, coll_name)

    sorted_hits = sorted(dedup.values(), key=lambda x: -x[2])[:k]
    return [
        {"text": t, "source": s, "score": round(sc, 3), "collection": c}
        for (t, s, sc, c) in sorted_hits
    ]


def build_rag_context(
    query: str,
    max_chars: int = 2500,
    header: str | None = None,
    use_multi: bool = True,
) -> str:
    """Build a prompt-ready context block from retrieved chunks.

    With ``use_multi=True`` (default), queries all 5 ChromaDB collections
    (knowledge_docs + actions + decisions + learnings + contacts) so the
    chat surfaces runtime activity alongside product docs. Set to False
    to restore the legacy single-collection behaviour.

    Returns an empty string if nothing is retrieved — callers should
    detect this and fall back to their static blob.
    """
    hits = hybrid_retrieve_multi(query, k=6) if use_multi else hybrid_retrieve(query, k=6)
    if not hits:
        return ""
    parts: list[str] = []
    if header:
        parts.append(header.strip())
    used = len("\n".join(parts))
    for h in hits:
        snippet = h["text"].strip()
        line = f"[{h['source']} | score={h['score']}] {snippet}"
        if used + len(line) + 2 > max_chars:
            break
        parts.append(line)
        used += len(line) + 2
    return "\n\n".join(parts)


def stats() -> dict:
    """Return collection size + cache size for the dashboard."""
    coll = _get_collection()
    if coll is None:
        return {"ok": False, "chunks": 0, "cache": 0}
    try:
        return {"ok": True, "chunks": int(coll.count()), "cache": len(_chunk_cache)}
    except Exception:
        return {"ok": False, "chunks": 0, "cache": 0}
