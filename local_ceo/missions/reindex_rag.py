"""Mission — nightly RAG re-index.

Re-ingests the default knowledge sources into the ``knowledge_docs``
ChromaDB collection whenever any source file has changed since the last
successful run.

Idempotent: ``rag_knowledge.ingest_docs()`` uses sha1-based doc IDs, so
running it on unchanged files is a no-op (``chunks_added = 0``). The
mtime gate here is a cheap short-circuit to avoid even computing
chunks+embeddings when nothing changed.

State persistence: stores the last run timestamp and per-file mtimes in
``local_ceo/rag_reindex_state.json``.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("ceo")

_HERE = Path(__file__).parent.parent
_STATE_FILE = _HERE / "rag_reindex_state.json"


def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("[reindex_rag] state load failed: %s", e)
    return {"last_run": 0, "mtimes": {}}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log.warning("[reindex_rag] state save failed: %s", e)


def _sources_changed(state: dict, sources: list[tuple[str, str]]) -> bool:
    """Check if any source file has a newer mtime than last run."""
    known = state.get("mtimes", {}) or {}
    for path, _tag in sources:
        if not os.path.exists(path):
            continue
        current = int(os.path.getmtime(path))
        if current != int(known.get(path, 0)):
            return True
    return False


def _record_mtimes(sources: list[tuple[str, str]]) -> dict:
    mtimes: dict[str, int] = {}
    for path, _tag in sources:
        if os.path.exists(path):
            mtimes[path] = int(os.path.getmtime(path))
    return mtimes


async def mission_reindex_rag(mem: dict | None = None, force: bool = False) -> dict:
    """Re-index the knowledge_docs collection if any source changed.

    Parameters
    ----------
    mem
        Unused — present for scheduler API consistency.
    force
        Ignore the mtime gate and re-ingest everything. Used by CLI
        debugging.

    Returns
    -------
    dict
        ``{"ran": bool, "stats": {...}}`` — ``ran=False`` when the mtime
        gate short-circuited.
    """
    try:
        import sys as _sys
        if str(_HERE) not in _sys.path:
            _sys.path.insert(0, str(_HERE))
        from rag_knowledge import DEFAULT_SOURCES, ingest_docs, stats
    except ImportError as e:
        log.warning("[reindex_rag] rag_knowledge unavailable: %s", e)
        return {"ran": False, "error": "rag_knowledge import failed"}

    state = _load_state()
    sources = list(DEFAULT_SOURCES)

    if not force and not _sources_changed(state, sources):
        log.info("[reindex_rag] no source changed since last run — skipping")
        return {"ran": False, "reason": "no_change"}

    log.info("[reindex_rag] running ingest (force=%s)", force)
    result = ingest_docs(sources=sources, force=force)

    # Persist new mtimes and last_run even if ingest had partial errors,
    # so we don't retry on the same mtimes forever on a broken file.
    state["last_run"] = int(time.time())
    state["mtimes"] = _record_mtimes(sources)
    state["last_stats"] = result
    state["last_collection"] = stats()
    _save_state(state)

    log.info(
        "[reindex_rag] done: +%d chunks, %d skipped, %d files in %.1fs",
        result.get("chunks_added", 0),
        result.get("skipped", 0),
        result.get("files", 0),
        result.get("elapsed_s", 0),
    )
    return {"ran": True, "stats": result}


if __name__ == "__main__":
    # CLI usage: `python missions/reindex_rag.py` or `... --force`
    import asyncio
    import sys

    force = "--force" in sys.argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    result = asyncio.run(mission_reindex_rag(force=force))
    print(json.dumps(result, indent=2, default=str))
