"""Memory — 3-layer persistent state for CEO Local.

Layer 1: Session (RAM) — current mission context, reset on restart.
Layer 2: Compressed (SQLite) — facts, learnings, actions, tweets, emails.
Layer 3: Vector (ChromaDB) — semantic search (vector_memory_local.py).
Backward-compatible: load_memory/save_memory/load_actions_today/save_actions.
"""
import json, logging, os, sqlite3, time
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("ceo")
_DIR = os.path.dirname(__file__)
_DB_PATH = os.path.join(_DIR, "ceo_state.db")
_OLD_MEMORY = os.path.join(_DIR, "ceo_memory.json")
_OLD_ACTIONS = os.path.join(_DIR, "actions_today.json")

try:
    from vector_memory_local import vmem as _vmem
except ImportError:
    _vmem = None

# ── Layer 1: Session (RAM) ──
_session: dict[str, Any] = {"current_mission": None, "started_at": 0.0, "actions": [], "errors": []}

def start_session(mission: str) -> None:
    _session.update(current_mission=mission, started_at=time.time(), actions=[], errors=[])
    log.info("[Session] Started: %s", mission)

def end_session() -> str:
    m = _session["current_mission"] or "unknown"
    elapsed = time.time() - _session["started_at"] if _session["started_at"] else 0
    summary = f"Mission '{m}': {len(_session['actions'])} actions, {len(_session['errors'])} errors, {elapsed:.1f}s"
    _session.update(current_mission=None, started_at=0.0, actions=[], errors=[])
    log.info("[Session] %s", summary)
    return summary

def session_log(msg: str) -> None:
    _session["actions"].append({"ts": time.time(), "msg": msg})

def session_error(msg: str) -> None:
    _session["errors"].append({"ts": time.time(), "msg": msg})

def get_session() -> dict[str, Any]:
    return {k: list(v) if isinstance(v, list) else v for k, v in _session.items()}

# ── Layer 2: SQLite ──
_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, date TEXT NOT NULL, type TEXT NOT NULL, target TEXT, details TEXT, created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS tweets(id INTEGER PRIMARY KEY, date TEXT NOT NULL, feature TEXT NOT NULL, text TEXT NOT NULL, engagement TEXT, created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS emails(id INTEGER PRIMARY KEY, direction TEXT NOT NULL, address TEXT NOT NULL, subject TEXT, body_preview TEXT, status TEXT DEFAULT 'sent', related_scout_id TEXT, created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY, platform TEXT NOT NULL, ext_id TEXT UNIQUE, text TEXT, score INTEGER, suggested_reply TEXT, email TEXT, status TEXT DEFAULT 'pending', created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS scout_agents(id INTEGER PRIMARY KEY, ext_id TEXT UNIQUE, name TEXT, registry TEXT, chain TEXT, score INTEGER, email TEXT, contact_message TEXT, status TEXT DEFAULT 'discovered', created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, date TEXT NOT NULL, type TEXT NOT NULL, data TEXT, created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS learnings(id INTEGER PRIMARY KEY, topic TEXT NOT NULL, insight TEXT NOT NULL, source TEXT, confidence REAL DEFAULT 0.5, times_confirmed INTEGER DEFAULT 0, times_contradicted INTEGER DEFAULT 0, last_used REAL, created_at REAL DEFAULT(unixepoch()));
CREATE TABLE IF NOT EXISTS strategy(id INTEGER PRIMARY KEY, week TEXT NOT NULL UNIQUE, objectives TEXT, score INTEGER, analysis TEXT, next_actions TEXT, created_at REAL DEFAULT(unixepoch()));
CREATE INDEX IF NOT EXISTS idx_actions_date ON actions(date);
CREATE INDEX IF NOT EXISTS idx_actions_type ON actions(date, type);
CREATE INDEX IF NOT EXISTS idx_tweets_date ON tweets(date);
CREATE INDEX IF NOT EXISTS idx_tweets_feature ON tweets(feature);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_learn_topic ON learnings(topic);
CREATE INDEX IF NOT EXISTS idx_learn_conf ON learnings(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_date ON metrics(date, type);
"""

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def _run(sql: str, params: tuple = (), *, fetch: bool = False, fetchone: bool = False, commit: bool = True) -> Any:
    """Execute SQL and return lastrowid, rows, or single row."""
    conn = _conn()
    try:
        cur = conn.execute(sql, params)
        if commit:
            conn.commit()
        if fetchone:
            r = cur.fetchone()
            return dict(r) if r else None
        if fetch:
            return [dict(r) for r in cur.fetchall()]
        return cur.lastrowid or 0
    finally:
        conn.close()

def init_db() -> None:
    conn = _conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        log.info("[Memory] SQLite OK: %s", _DB_PATH)
    finally:
        conn.close()

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

# ── Logging functions ──

def log_action(action_type: str, target: str | None = None, details: str | None = None) -> int:
    rid = _run("INSERT INTO actions(date,type,target,details) VALUES(?,?,?,?)",
               (_today(), action_type, target, details))
    if _vmem and details:
        _vmem.store_action(action_type, target or "", details)
    return rid

def log_tweet(feature: str, text: str) -> int:
    return _run("INSERT INTO tweets(date,feature,text) VALUES(?,?,?)", (_today(), feature, text))

def log_email(direction: str, address: str, subject: str | None = None,
              body_preview: str | None = None, status: str = "sent",
              related_scout_id: str | None = None) -> int:
    return _run("INSERT INTO emails(direction,address,subject,body_preview,status,related_scout_id) VALUES(?,?,?,?,?,?)",
                (direction, address, subject, body_preview, status, related_scout_id))

def log_opportunity(platform: str, ext_id: str, text: str | None = None,
                    score: int = 0, reply: str | None = None, email: str | None = None) -> int:
    return _run("INSERT INTO opportunities(platform,ext_id,text,score,suggested_reply,email) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(ext_id) DO UPDATE SET score=excluded.score,suggested_reply=excluded.suggested_reply,text=excluded.text",
                (platform, ext_id, text, score, reply, email))

def log_metric(metric_type: str, data: dict | str | None = None) -> int:
    d = json.dumps(data, default=str) if isinstance(data, dict) else data
    return _run("INSERT INTO metrics(date,type,data) VALUES(?,?,?)", (_today(), metric_type, d))

# ── Queries ──

def get_today_actions(action_type: str | None = None) -> list[dict]:
    if action_type:
        return _run("SELECT * FROM actions WHERE date=? AND type=? ORDER BY created_at",
                     (_today(), action_type), fetch=True)
    return _run("SELECT * FROM actions WHERE date=? ORDER BY created_at", (_today(),), fetch=True)

def get_today_action_count(action_type: str) -> int:
    r = _run("SELECT COUNT(*) AS cnt FROM actions WHERE date=? AND type=?",
             (_today(), action_type), fetchone=True)
    return r["cnt"] if r else 0

def get_recent_tweets(days: int = 14) -> list[dict]:
    return _run("SELECT * FROM tweets WHERE date>=? ORDER BY created_at DESC",
                (_cutoff(days),), fetch=True)

def was_feature_tweeted_recently(feature: str, days: int = 7) -> bool:
    r = _run("SELECT COUNT(*) AS cnt FROM tweets WHERE feature=? AND date>=?",
             (feature, _cutoff(days)), fetchone=True)
    return (r["cnt"] if r else 0) > 0

# ── Learnings ──

def jaccard_similarity(a: str, b: str) -> float:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0

def compress_and_store_learning(insight: str, source: str = "",
                                topic: str = "general", confidence: float = 0.5) -> int:
    existing = _run("SELECT id,insight,times_confirmed,confidence FROM learnings "
                    "WHERE topic=? ORDER BY confidence DESC LIMIT 50", (topic,), fetch=True)
    for row in existing:
        if jaccard_similarity(insight, row["insight"]) > 0.6:
            nc, nconf = row["times_confirmed"] + 1, min(1.0, row["confidence"] + 0.1)
            _run("UPDATE learnings SET times_confirmed=?,confidence=?,last_used=? WHERE id=?",
                 (nc, nconf, time.time(), row["id"]))
            log.info("[Memory] Learning confirmed (id=%d, conf=%.2f)", row["id"], nconf)
            return row["id"]
    rid = _run("INSERT INTO learnings(topic,insight,source,confidence) VALUES(?,?,?,?)",
               (topic, insight, source, confidence))
    if _vmem:
        _vmem.store_learning(insight, source=source)
    log.info("[Memory] New learning (id=%d, topic=%s)", rid, topic)
    return rid

def get_relevant_learnings(topic: str, limit: int = 5) -> list[dict]:
    results = _run("SELECT * FROM learnings WHERE topic=? ORDER BY confidence DESC,times_confirmed DESC LIMIT ?",
                   (topic, limit), fetch=True)
    if _vmem and len(results) < limit:
        for vr in _vmem.search(topic, collection="learnings", n=limit):
            if not any(jaccard_similarity(vr["text"], r["insight"]) > 0.6 for r in results):
                results.append({"topic": topic, "insight": vr["text"], "source": "vector", "confidence": vr["score"]})
                if len(results) >= limit:
                    break
    return results

# ── Weekly metrics ──

def get_weekly_metrics() -> dict[str, Any]:
    monday = datetime.now() - timedelta(days=datetime.now().weekday())
    ms = monday.strftime("%Y-%m-%d")
    acts = _run("SELECT type,COUNT(*) AS cnt FROM actions WHERE date>=? GROUP BY type", (ms,), fetch=True)
    tw = _run("SELECT COUNT(*) AS cnt FROM tweets WHERE date>=?", (ms,), fetchone=True)
    em = _run("SELECT COUNT(*) AS cnt FROM emails WHERE created_at>=?", (monday.timestamp(),), fetchone=True)
    op = _run("SELECT COUNT(*) AS cnt FROM opportunities WHERE created_at>=?", (monday.timestamp(),), fetchone=True)
    return {"week_start": ms, "actions_by_type": {r["type"]: r["cnt"] for r in acts},
            "tweets": tw["cnt"] if tw else 0, "emails": em["cnt"] if em else 0,
            "opportunities": op["cnt"] if op else 0}

# ── Cleanup ──

def cleanup_old_data(days: int = 90) -> dict[str, int]:
    cutoff_d, cutoff_ts = _cutoff(days), time.time() - days * 86400
    deleted = {}
    conn = _conn()
    try:
        for tbl, col in [("actions","date"),("tweets","date"),("emails","created_at"),
                         ("opportunities","created_at"),("metrics","date")]:
            v = cutoff_d if col == "date" else cutoff_ts
            deleted[tbl] = conn.execute(f"DELETE FROM {tbl} WHERE {col}<?", (v,)).rowcount
        conn.commit()
        t = sum(deleted.values())
        if t:
            log.info("[Memory] Cleanup: %d rows purged (>%dd)", t, days)
        return deleted
    finally:
        conn.close()

# ── Migration from JSON ──

def migrate_json_to_sqlite() -> dict[str, int]:
    migrated: dict[str, int] = {"actions": 0, "tweets": 0, "opportunities": 0}
    r = _run("SELECT COUNT(*) AS cnt FROM actions", fetchone=True)
    if r and r["cnt"] > 0:
        return migrated
    conn = _conn()
    try:
        if os.path.exists(_OLD_MEMORY):
            try:
                mem = json.loads(open(_OLD_MEMORY, encoding="utf-8").read())
                for tw in mem.get("tweets_posted", []):
                    d, f, t = (_today(), "unknown", str(tw))
                    if isinstance(tw, dict):
                        d, f, t = tw.get("date", d), tw.get("feature", f), tw.get("text", t)
                    conn.execute("INSERT INTO tweets(date,feature,text) VALUES(?,?,?)", (d, f, t))
                    migrated["tweets"] += 1
                for opp in mem.get("opportunities_sent", []):
                    if isinstance(opp, dict):
                        conn.execute("INSERT OR IGNORE INTO opportunities(platform,ext_id,text,score,status) VALUES(?,?,?,?,'sent')",
                                     (opp.get("platform","unknown"), opp.get("id",str(hash(str(opp)))),
                                      opp.get("text",str(opp)), opp.get("score",0)))
                        migrated["opportunities"] += 1
                conn.commit()
            except Exception as e:
                log.error("[Memory] Migration ceo_memory.json: %s", e)
        if os.path.exists(_OLD_ACTIONS):
            try:
                act = json.loads(open(_OLD_ACTIONS, encoding="utf-8").read())
                date = act.get("date", _today())
                for atype, count in act.get("counts", {}).items():
                    for _ in range(min(count, 50)):
                        conn.execute("INSERT INTO actions(date,type,target) VALUES(?,?,'migrated')", (date, atype))
                        migrated["actions"] += 1
                conn.commit()
            except Exception as e:
                log.error("[Memory] Migration actions_today.json: %s", e)
        log.info("[Memory] Migrated: %s", migrated)
        return migrated
    finally:
        conn.close()

# ── Backward-compatible API (ceo_main.py) ──

def load_memory() -> dict:
    default: dict[str, Any] = {
        "tweets_posted": [], "opportunities_sent": [], "repos_scanned": [],
        "agents_seen": [], "sites_found": [], "moderation_log": [],
        "health_alerts": [], "feature_index": 0, "regles": [],
        "tweet_engagement": [], "competitive_reports": [],
        "todays_opportunities": [], "todays_github_opportunities": [],
    }
    try:
        if os.path.exists(_OLD_MEMORY):
            data = json.loads(open(_OLD_MEMORY, encoding="utf-8").read())
            for k, v in default.items():
                data.setdefault(k, v)
            return data
    except Exception as e:
        log.error("Memory load: %s", e)
    return default

def save_memory(mem: dict) -> None:
    for k in ["tweets_posted","opportunities_sent","moderation_log","health_alerts","tweet_engagement","competitive_reports"]:
        if len(mem.get(k, [])) > 200:
            mem[k] = mem[k][-200:]
    if len(mem.get("agents_seen", [])) > 500:
        mem["agents_seen"] = mem["agents_seen"][-500:]
    try:
        open(_OLD_MEMORY, "w", encoding="utf-8").write(json.dumps(mem, indent=2, default=str, ensure_ascii=False))
    except Exception as e:
        log.error("Memory save: %s", e)

def load_actions_today() -> dict:
    default: dict[str, Any] = {"date": "", "counts": {"tweet_feature": 0, "opportunities_sent": 0,
                                "report_sent": 0, "moderation_done": 0, "health_checks": 0}}
    try:
        if os.path.exists(_OLD_ACTIONS):
            data = json.loads(open(_OLD_ACTIONS, encoding="utf-8").read())
            if data.get("date") != _today():
                data = {**default, "date": _today()}
            return data
    except Exception:
        pass
    return {**default, "date": _today()}

def save_actions(actions: dict) -> None:
    try:
        open(_OLD_ACTIONS, "w", encoding="utf-8").write(json.dumps(actions, indent=2))
    except Exception:
        pass

# ── Init on import ──
init_db()
