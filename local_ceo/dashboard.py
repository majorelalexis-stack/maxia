"""Dashboard local CEO MAXIA — http://localhost:8888

Zero dependance externe. Controles: pause/resume CEO, approuver actions ORANGE.

Sources de verite (CEO V3+V9):
- ceo_state.db      : actions/tweets/emails/opportunities SQL (canon)
- actions_today.json: compteurs par type du jour courant
- ceo_memory.json   : legacy regles/history (load_memory API)
- ceo_main.log      : logs temps reel du CEO V3 (FileHandler dans ceo_main.py)
- VPS /api/ceo/messages/status : stats bridge Discord/Forum
"""
import json, os, time, sqlite3, urllib.parse, traceback
from pathlib import Path
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler

_DIR = Path(__file__).parent
_MEMORY_FILE = _DIR / "ceo_memory.json"
_STATE_DB = _DIR / "ceo_state.db"  # CEO V3 canonical SQL DB
_ACTIONS_TODAY_FILE = _DIR / "actions_today.json"
_LOG_FILE = _DIR / "ceo_main.log"  # written by ceo_main.py FileHandler
_CONTROL_FILE = _DIR / "ceo_control.json"  # pause/resume + settings
_ALEXIS_CHAT_FILE = _DIR / "alexis_chat_log.json"  # persistent chat Alexis <-> CEO
_ALEXIS_CHAT_MAX_TURNS = 200  # rolling cap (~100 exchanges)

# Bridge VPS (Discord / Forum / Inbox auto-reply)
try:
    import sys as _sys
    _sys.path.insert(0, str(_DIR))
    from config_local import VPS_URL as _VPS_URL  # type: ignore
except Exception:
    _VPS_URL = os.environ.get("VPS_URL", "https://maxiaworld.app")


def _load_memory() -> dict:
    try:
        if _MEMORY_FILE.exists():
            raw = _MEMORY_FILE.read_text(encoding="utf-8")
            # Handle encrypted memory (Fernet)
            if raw.startswith("gAAAAA"):
                try:
                    key_file = _DIR / ".memory_key"
                    if key_file.exists():
                        from cryptography.fernet import Fernet
                        key = key_file.read_bytes()
                        raw = Fernet(key).decrypt(raw.encode()).decode()
                except Exception:
                    # Try backup
                    bak = Path(str(_MEMORY_FILE) + ".bak")
                    if bak.exists():
                        raw = bak.read_text(encoding="utf-8")
                    else:
                        return {}
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _save_memory(mem: dict):
    try:
        _MEMORY_FILE.write_text(json.dumps(mem, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_control() -> dict:
    try:
        if _CONTROL_FILE.exists():
            return json.loads(_CONTROL_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"paused": False, "interval_s": 600}


def _save_control(ctrl: dict):
    _CONTROL_FILE.write_text(json.dumps(ctrl, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
#  Alexis <-> CEO chat (local web interface, replaces Telegram)
# ─────────────────────────────────────────────────────────────


def _load_alexis_chat() -> list:
    """Return the persistent chat history as a list of {role, content, ts}."""
    try:
        if _ALEXIS_CHAT_FILE.exists():
            data = json.loads(_ALEXIS_CHAT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_alexis_chat(history: list) -> None:
    """Persist the chat history, capping at ``_ALEXIS_CHAT_MAX_TURNS`` entries."""
    trimmed = history[-_ALEXIS_CHAT_MAX_TURNS:]
    try:
        _ALEXIS_CHAT_FILE.write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _alexis_chat_reply(user_message: str) -> dict:
    """Call the CEO local legacy flow (user_id=None) and append both turns to
    the persistent history. Returns ``{reply, latency_ms, history_len, error?}``.

    Runs the async ``answer_user_message`` via ``asyncio.run``. Never raises —
    on any failure returns ``{error: ...}`` so the dashboard stays responsive.
    """
    import asyncio
    import sys as _sys_local
    _sys_local.path.insert(0, str(_DIR))

    msg = (user_message or "").strip()
    if not msg:
        return {"error": "empty message"}
    if len(msg) > 4000:
        return {"error": "message too long (max 4000 chars)"}

    # Preflight: if httpx is missing, we know the LLM call will fail.
    # Surface this as a clear error instead of a confusing fallback.
    try:
        import httpx  # noqa: F401
    except ImportError:
        return {
            "error": (
                "httpx not installed in this Python. Dashboard was launched "
                "with a Python that lacks MAXIA dependencies. Relaunch with: "
                f"python {_DIR / 'dashboard.py'} using the same interpreter "
                "that runs ceo_main.py."
            ),
            "reply": "",
            "latency_ms": 0,
            "history_len": len(_load_alexis_chat()),
        }

    history = _load_alexis_chat()

    # Legacy flow expects history items as {role, content} — strip timestamps.
    # Cap to last 6 turns (3 user + 3 assistant): longer history + the
    # RUNTIME_STATE blob + KNOWLEDGE block overflow qwen3's 8192 ctx
    # window, causing empty replies or hallucinations. We still keep
    # the full history on disk for audit, we just don't send it all to
    # the LLM every turn.
    hist_for_llm = [
        {"role": h.get("role", "user"), "content": (h.get("content", "") or "")[:400]}
        for h in history[-6:]
    ]

    # Load the live memory + today's counters from disk so the chat
    # sees the same RUNTIME_STATE as the Telegram channel handler. The
    # dashboard runs in a separate process from ceo_main.py, so we
    # cannot share in-memory dicts — disk is the sync point.
    mem_snapshot = None
    actions_snapshot = None
    try:
        from memory import load_memory, load_actions_today
        mem_snapshot = load_memory()
        actions_snapshot = load_actions_today()
    except Exception as _e:
        print(f"[dashboard] runtime snapshot failed: {_e}", flush=True)

    t0 = time.time()
    reply = ""
    error = None
    try:
        from missions.telegram_smart_reply import answer_user_message
        reply = asyncio.run(answer_user_message(
            user_message=msg,
            history=hist_for_llm,
            language_code=None,
            user_id=None,     # <-- critical: None = legacy flow = Alexis assistant
            channel="web",
            mem=mem_snapshot,
            actions_today=actions_snapshot,
        ))
    except Exception as e:
        tb = traceback.format_exc(limit=3)
        error = f"{type(e).__name__}: {e}\n{tb}"
        reply = "Sorry, the local CEO could not generate a reply. Check the dashboard stderr."
        try:
            print(f"[dashboard] chat error: {error}", flush=True)
        except Exception:
            pass

    latency_ms = int((time.time() - t0) * 1000)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    history.append({"role": "user", "content": msg, "ts": now_iso})
    history.append({
        "role": "assistant",
        "content": reply,
        "ts": now_iso,
        "latency_ms": latency_ms,
    })
    _save_alexis_chat(history)

    out = {
        "reply": reply,
        "latency_ms": latency_ms,
        "history_len": len(history),
    }
    if error:
        out["error"] = error
    return out


def _read_log(lines: int = 50) -> list:
    """Read the tail of the CEO V3 main log.

    Returns the last ``lines`` lines, or an empty list if the log file
    doesn't exist (e.g. CEO hasn't booted yet). Large log files are
    tailed with a byte offset to avoid loading the whole file.
    """
    try:
        if _LOG_FILE.exists():
            # Tail ~32 KB — enough for ~200 log lines
            size = _LOG_FILE.stat().st_size
            tail_bytes = 32 * 1024
            with open(_LOG_FILE, "rb") as f:
                if size > tail_bytes:
                    f.seek(size - tail_bytes)
                raw = f.read().decode("utf-8", errors="replace")
            all_lines = raw.strip().split("\n")
            return all_lines[-lines:]
    except Exception:
        pass
    return []


def _read_audit(limit: int = 30) -> list:
    """Read the most recent actions from ceo_state.db.

    Returns a list of dicts shaped like the old ``ceo_audit`` rows the
    dashboard HTML expects:
        {timestamp, action, priority, success, result}
    """
    if not _STATE_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_STATE_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,date,type,target,details,created_at "
            "FROM actions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    except Exception:
        return []

    out = []
    for r in rows:
        created = r["created_at"]
        if isinstance(created, (int, float)) and created > 0:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(created))
        else:
            ts = (r["date"] or "") + "T00:00:00"
        details = r["details"] or ""
        target = r["target"] or ""
        out.append(
            {
                "timestamp": ts,
                "action": r["type"] or "",
                "priority": "vert",
                "success": 0 if (details or "").startswith("ERROR") else 1,
                "result": (target + " " + details).strip()[:200],
            }
        )
    return out


def _read_actions_today_counts() -> dict:
    """Read actions_today.json — canonical source for 'today' counters."""
    try:
        if _ACTIONS_TODAY_FILE.exists():
            data = json.loads(_ACTIONS_TODAY_FILE.read_text(encoding="utf-8"))
            # Only return counts for today (CEO rotates the file each day)
            today = time.strftime("%Y-%m-%d")
            if data.get("date") == today:
                return dict(data.get("counts") or {})
    except Exception:
        pass
    return {}


def _get_state_totals() -> dict:
    """Totals from ceo_state.db: actions total, tweets total, opportunities."""
    totals = {"actions_total": 0, "tweets_total": 0, "opportunities": 0}
    if not _STATE_DB.exists():
        return totals
    try:
        conn = sqlite3.connect(str(_STATE_DB))
        for key, sql in [
            ("actions_total", "SELECT COUNT(*) FROM actions"),
            ("tweets_total", "SELECT COUNT(*) FROM tweets"),
            ("opportunities", "SELECT COUNT(*) FROM opportunities"),
        ]:
            try:
                totals[key] = int(conn.execute(sql).fetchone()[0])
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return totals


def _get_bridge_status() -> dict:
    """Poll VPS /api/ceo/messages/status (no auth). Silent fallback.

    Returns a dict like ``{"ok": bool, "counters": {...}, "channels": [...]}``.
    Short timeout so the dashboard stays snappy when the VPS is down.
    """
    try:
        import urllib.request  # stdlib only — no httpx dep on dashboard
        url = f"{_VPS_URL.rstrip('/')}/api/ceo/messages/status"
        req = urllib.request.Request(url, headers={"User-Agent": "maxia-dashboard"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:  # noqa: S310
            if resp.status != 200:
                return {"ok": False, "error": f"HTTP {resp.status}"}
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return {
                "ok": True,
                "counters": dict(data.get("counters") or {}),
                "channels": list(data.get("channels") or []),
            }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def _get_dashboard_data() -> dict:
    mem = _load_memory()
    ctrl = _load_control()
    audit = _read_audit(20)
    today_counts = _read_actions_today_counts()
    totals = _get_state_totals()
    bridge = _get_bridge_status()

    # Canonical counters:
    # - actions_today  = sum of counts in actions_today.json (CEO V3 canon)
    # - actions_total  = row count in ceo_state.db (all history)
    # - tweets_posted  = row count in tweets table
    actions_today = sum(int(v) for v in today_counts.values())
    # Recent actions (last 10) synthesized from the audit list
    recent_actions = [
        {
            "action": a.get("action", ""),
            "success": bool(a.get("success", 1)),
            "ts": a.get("timestamp", ""),
        }
        for a in audit[:10]
    ]

    return {
        "cycle_count": int(mem.get("cycle_count", 0)),
        "decisions_total": len(mem.get("decisions", [])),
        "actions_total": totals["actions_total"],
        "actions_today": actions_today,
        "actions_today_by_type": today_counts,  # {disboard_bumps: 5, ...}
        "recent_decisions": mem.get("decisions", [])[-10:],
        "recent_actions": recent_actions,
        "audit": audit[:15],
        "logs": _read_log(40),
        "tweets_posted": totals["tweets_total"],
        "opportunities": totals["opportunities"],
        "follows": len(mem.get("follows", [])),
        "contacts": len(mem.get("contacts", [])),
        "paused": ctrl.get("paused", False),
        "interval_s": ctrl.get("interval_s", 600),
        "regles": mem.get("regles", [])[-5:],
        # Historique 7 jours pour graphiques
        "daily_history": _compute_daily_history(mem),
        "crm": {
            "contacts": len(mem.get("contacts", [])),
            "follows": len(mem.get("follows", [])),
            "groups": len(mem.get("groups_joined", [])),
            "groups_list": mem.get("groups_joined", [])[-5:],
        },
        "pending_approvals": _get_pending_approvals(),
        "sales": _get_sales_snapshot(),
        "bridge": bridge,  # Discord/Forum/Inbox auto-reply bridge status
    }


def _get_pending_approvals() -> list:
    """Lit les approbations en attente."""
    try:
        from notifier import get_pending_approvals
        return get_pending_approvals()
    except Exception:
        return []


def _get_sales_snapshot() -> dict:
    """MaxiaSalesAgent metrics — silent fallback if not deployed.

    Returns the snapshot dict if the sales DB exists, otherwise an
    empty dict so the dashboard JS can render a placeholder.
    """
    try:
        from sales.dashboard import snapshot
        return snapshot()
    except Exception:
        return {}


def _get_sales_conversation(conversation_id: str) -> dict:
    """Full history + telemetry for a single sales conversation.

    Used by the modal "view conversation" feature in the main dashboard.
    """
    try:
        from sales.dashboard import get_conversation
        return get_conversation(conversation_id)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _compute_daily_history(mem: dict) -> list:
    """Calcule les stats par jour sur 7 jours pour les graphiques."""
    from collections import defaultdict
    import datetime
    days = defaultdict(lambda: {"actions": 0, "tweets": 0, "likes": 0, "follows": 0, "success": 0, "fail": 0})
    for a in mem.get("actions_done", []):
        day = a.get("ts", "")[:10]
        if not day:
            continue
        days[day]["actions"] += 1
        if a.get("success"):
            days[day]["success"] += 1
        else:
            days[day]["fail"] += 1
        act = a.get("action", "")
        if "tweet" in act:
            days[day]["tweets"] += 1
        elif "like" in act:
            days[day]["likes"] += 1
        elif "follow" in act:
            days[day]["follows"] += 1

    # Derniers 7 jours
    today = datetime.date.today()
    result = []
    for i in range(6, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        data = days.get(d, {"actions": 0, "tweets": 0, "likes": 0, "follows": 0, "success": 0, "fail": 0})
        data["date"] = d[5:]  # MM-DD
        result.append(data)
    return result


_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CEO MAXIA Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0f;color:#e0e0e0;font-family:'Segoe UI',sans-serif;padding:20px;max-width:1200px;margin:0 auto}
h1{color:#00ff88;font-size:22px;margin-bottom:15px;display:flex;justify-content:space-between;align-items:center}
.controls{display:flex;gap:8px}
.btn{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold}
.btn-pause{background:#ff8c00;color:#000}
.btn-resume{background:#00ff88;color:#000}
.btn-danger{background:#ff4444;color:#fff}
.btn-small{padding:5px 10px;font-size:11px}
.status{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:bold}
.status-run{background:#00ff8822;color:#00ff88;border:1px solid #00ff88}
.status-pause{background:#ff8c0022;color:#ff8c00;border:1px solid #ff8c00}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:20px}
.card{background:#151520;border:1px solid #252535;border-radius:8px;padding:14px;text-align:center}
.card .num{font-size:28px;font-weight:bold;color:#00ff88}
.card .label{font-size:11px;color:#888;margin-top:3px}
.section{background:#151520;border:1px solid #252535;border-radius:8px;padding:14px;margin-bottom:15px}
.section h2{color:#00ff88;font-size:14px;margin-bottom:8px;border-bottom:1px solid #252535;padding-bottom:6px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:15px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#888;padding:4px 6px;border-bottom:1px solid #252535}
td{padding:4px 6px;border-bottom:1px solid #1a1a2a;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vert{color:#00ff88}.orange{color:#ff8c00}.rouge{color:#ff4444}
.ok{color:#00ff88}.fail{color:#ff4444}
.log-box{background:#0a0a12;border-radius:6px;padding:10px;font-family:monospace;font-size:11px;max-height:220px;overflow-y:auto;white-space:pre-wrap;line-height:1.4}
.refresh{color:#555;font-size:11px;text-align:right;margin-top:5px}
.setting{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:13px}
.setting input{background:#0a0a12;color:#e0e0e0;border:1px solid #252535;padding:5px 8px;border-radius:4px;width:80px}
.setting label{color:#888;min-width:120px}
</style>
</head>
<body>
<h1>
  CEO MAXIA Dashboard
  <div class="controls">
    <span id="status" class="status status-run">ACTIF</span>
    <button id="pauseBtn" class="btn btn-pause" onclick="togglePause()">Pause</button>
    <button id="reindexBtn" class="btn btn-small" style="background:#252535;color:#e0e0e0" onclick="reindexRag()">Reindex RAG</button>
    <button class="btn btn-small" style="background:#252535;color:#e0e0e0" onclick="exportCSV()">Export CSV</button>
    <button class="btn btn-danger btn-small" onclick="clearMemory()">Reset memoire</button>
  </div>
</h1>

<!-- Alexis <-> CEO chat (local, replaces Telegram bot) -->
<div class="section" id="alexis_chat">
  <h2 style="display:flex;justify-content:space-between;align-items:center">
    <span>Chat avec le CEO local (qwen3:30b-a3b + RAG 155 chunks)</span>
    <button class="btn btn-small btn-danger" onclick="clearAlexisChat()">Clear</button>
  </h2>
  <div id="chat_bubbles" style="background:#0a0a12;border-radius:6px;padding:12px;height:320px;overflow-y:auto;font-size:13px;line-height:1.5;margin-bottom:10px"></div>
  <div style="display:flex;gap:8px;align-items:flex-end">
    <textarea id="chat_input" rows="2" placeholder="Pose une question, demande un draft, colle une reponse a reformuler... (Ctrl+Enter pour envoyer)" style="flex:1;background:#0a0a12;color:#e0e0e0;border:1px solid #252535;border-radius:6px;padding:10px;font-family:inherit;font-size:13px;resize:vertical;min-height:44px"></textarea>
    <button id="chat_send" class="btn" style="background:#00ff88;color:#000;padding:10px 20px" onclick="sendAlexisChat()">Send</button>
  </div>
  <div id="chat_status" style="color:#555;font-size:11px;margin-top:6px">&nbsp;</div>
</div>

<div class="grid" id="kpis"></div>

<div class="section" id="sales_section">
  <h2>MaxiaSalesAgent — sales conversations</h2>
  <div class="grid" id="sales_kpis" style="margin-bottom:10px"></div>
  <div class="cols">
    <div>
      <div style="font-size:11px;color:#888;margin-bottom:6px">Stage breakdown (funnel)</div>
      <div id="sales_stages"></div>
    </div>
    <div>
      <div style="font-size:11px;color:#888;margin-bottom:6px">Channels</div>
      <div id="sales_channels" style="margin-bottom:8px"></div>
      <div style="font-size:11px;color:#888;margin-bottom:6px">Languages</div>
      <div id="sales_langs"></div>
    </div>
  </div>
  <div style="margin-top:12px">
    <div style="font-size:11px;color:#888;margin-bottom:6px">Latency &amp; throughput (last 7d)</div>
    <div id="sales_latency"></div>
  </div>
  <div style="margin-top:12px">
    <div style="font-size:11px;color:#888;margin-bottom:6px">Active conversations (click to view full history)</div>
    <div id="sales_active"></div>
  </div>
</div>

<!-- Conversation history modal -->
<div id="convModal" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.85);z-index:1000;align-items:center;justify-content:center">
  <div style="background:#151520;border:1px solid #00ff88;border-radius:8px;padding:20px;max-width:820px;max-height:85vh;overflow-y:auto;width:92%">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;border-bottom:1px solid #252535;padding-bottom:8px">
      <div>
        <div id="convModalTitle" style="color:#00ff88;font-size:14px;font-weight:bold"></div>
        <div id="convModalMeta" style="color:#888;font-size:11px;margin-top:2px"></div>
      </div>
      <button class="btn btn-small btn-danger" onclick="closeConvModal()">Close</button>
    </div>
    <div id="convModalBody" style="font-size:12px;line-height:1.5"></div>
  </div>
</div>

<div class="section">
  <h2>Controles</h2>
  <div class="setting">
    <label>Intervalle (sec):</label>
    <input id="interval" type="number" value="600" onchange="updateInterval(this.value)">
    <span style="color:#555">Cycle OODA toutes les X secondes</span>
  </div>
  <div class="setting">
    <label>Ajouter regle:</label>
    <input id="newRule" type="text" style="width:400px" placeholder="Ex: Ne jamais poster apres 22h UTC">
    <button class="btn btn-small" style="background:#252535;color:#e0e0e0" onclick="addRule()">Ajouter</button>
  </div>
</div>

<div class="section"><h2>Activite 7 jours</h2><div id="chart"></div></div>

<div class="cols">
<div class="section">
  <h2>Missions V9 — today</h2>
  <div id="missions_today" style="font-size:12px"></div>
</div>
<div class="section">
  <h2>Bridge Discord / Forum / Inbox</h2>
  <div id="bridge_status" style="font-size:12px"></div>
</div>
</div>

<div class="cols">
<div class="section"><h2>Decisions recentes</h2><table id="decisions"></table></div>
<div class="section"><h2>Actions executees</h2><table id="actions"></table></div>
</div>
<div class="section"><h2>Audit</h2><table id="audit"></table></div>
<div class="cols">
<div class="section"><h2>CRM</h2><div id="crm" style="font-size:12px"></div></div>
<div class="section"><h2>Approbations en attente</h2><div id="approvals" style="font-size:12px"></div></div>
</div>
<div class="section"><h2>Regles actives</h2><div id="regles" style="font-size:12px;color:#aaa"></div></div>
<div class="section"><h2>Logs</h2><div class="log-box" id="logs"></div></div>
<div class="refresh" id="refresh"></div>
<script>
// ─── Alexis <-> CEO chat (local) ─────────────────────────────
function escapeHtml(s){
  s = String(s == null ? '' : s);
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\x22/g,'&quot;').replace(/'/g,'&#39;');
}
function renderChatBubbles(history){
  var box=document.getElementById('chat_bubbles');
  if(!box) return;
  if(!history || history.length===0){
    box.innerHTML='<div style="color:#555;text-align:center;padding:20px">Chat vide. Ecris ta premiere question au CEO ci-dessous.</div>';
    return;
  }
  var html='';
  history.forEach(function(t){
    var role=t.role||'user';
    var content=escapeHtml(t.content||'');
    var ts=t.ts||'';
    var latency=t.latency_ms?' <span style="color:#555">('+t.latency_ms+'ms)</span>':'';
    if(role==='user'){
      html+='<div style="display:flex;justify-content:flex-end;margin:8px 0">'+
        '<div style="background:#0066cc33;border:1px solid #0066cc;border-radius:10px 10px 2px 10px;padding:8px 12px;max-width:75%;color:#cce5ff;white-space:pre-wrap">'+content+
        '<div style="color:#888;font-size:10px;margin-top:4px">'+ts+'</div></div></div>';
    }else{
      html+='<div style="display:flex;justify-content:flex-start;margin:8px 0">'+
        '<div style="background:#00ff8822;border:1px solid #00ff88;border-radius:10px 10px 10px 2px;padding:8px 12px;max-width:85%;color:#ccffdd;white-space:pre-wrap">'+content+
        '<div style="color:#888;font-size:10px;margin-top:4px">'+ts+latency+'</div></div></div>';
    }
  });
  box.innerHTML=html;
  box.scrollTop=box.scrollHeight;
}
async function loadAlexisChat(){
  try{
    var r=await fetch('/api/chat/alexis/history');
    var d=await r.json();
    renderChatBubbles(d.history||[]);
  }catch(e){}
}
async function sendAlexisChat(){
  var input=document.getElementById('chat_input');
  var btn=document.getElementById('chat_send');
  var status=document.getElementById('chat_status');
  var msg=(input.value||'').trim();
  if(!msg) return;
  btn.disabled=true;
  btn.style.opacity='0.5';
  var t0=Date.now();
  status.textContent='CEO is thinking... (~3-5s with qwen3:30b)';
  // Optimistic render of user turn
  var cur=document.getElementById('chat_bubbles');
  cur.innerHTML+='<div style="display:flex;justify-content:flex-end;margin:8px 0">'+
    '<div style="background:#0066cc33;border:1px solid #0066cc;border-radius:10px 10px 2px 10px;padding:8px 12px;max-width:75%;color:#cce5ff;white-space:pre-wrap">'+escapeHtml(msg)+
    '<div style="color:#888;font-size:10px;margin-top:4px">sending...</div></div></div>';
  cur.scrollTop=cur.scrollHeight;
  try{
    var r=await fetch('/api/chat/alexis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})});
    var d=await r.json();
    if(d.error && !d.reply){
      status.textContent='Error: '+d.error;
    }else{
      input.value='';
      var ms=Date.now()-t0;
      status.textContent='OK — reply in '+(d.latency_ms||ms)+'ms, history: '+(d.history_len||'?')+' turns'+(d.error?' (warn: '+d.error+')':'');
    }
    await loadAlexisChat();
  }catch(e){
    status.textContent='Network error: '+e;
  }finally{
    btn.disabled=false;
    btn.style.opacity='1';
    input.focus();
  }
}
async function clearAlexisChat(){
  if(!confirm('Clear the chat history with the local CEO?')) return;
  try{
    await fetch('/api/chat/alexis/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    await loadAlexisChat();
  }catch(e){}
}
// Init immediately — the script tag is at the bottom of the body so the DOM
// elements above (chat_bubbles, chat_input, chat_send) already exist.
// We do NOT rely on DOMContentLoaded because it may have already fired by
// the time this inline script runs, which would skip the init entirely.
try{
  loadAlexisChat();
  var _chatInput=document.getElementById('chat_input');
  if(_chatInput){
    _chatInput.addEventListener('keydown',function(e){
      if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){
        e.preventDefault();
        sendAlexisChat();
      }
    });
  }
  // Poll history every 30s in case CEO writes unsolicited
  setInterval(loadAlexisChat,30000);
  console.log('[chat] init OK');
}catch(_e){
  console.error('[chat] init failed:',_e);
  var _box=document.getElementById('chat_bubbles');
  if(_box) _box.innerHTML='<div style="color:#ff4444;padding:20px">Chat init failed: '+String(_e)+' — hard refresh the page (Ctrl+F5)</div>';
}

let isPaused=false;
async function load(){
  try{
    const r=await fetch('/api/dashboard');
    const d=await r.json();
    isPaused=d.paused;
    document.getElementById('status').className='status '+(isPaused?'status-pause':'status-run');
    document.getElementById('status').textContent=isPaused?'PAUSE':'ACTIF';
    document.getElementById('pauseBtn').textContent=isPaused?'Resume':'Pause';
    document.getElementById('pauseBtn').className='btn '+(isPaused?'btn-resume':'btn-pause');
    document.getElementById('interval').value=d.interval_s;
    document.getElementById('kpis').innerHTML=
      `<div class="card"><div class="num">${d.cycle_count}</div><div class="label">Cycles</div></div>`+
      `<div class="card"><div class="num">${d.decisions_total}</div><div class="label">Decisions</div></div>`+
      `<div class="card"><div class="num">${d.actions_today}</div><div class="label">Actions today</div></div>`+
      `<div class="card"><div class="num">${d.actions_total}</div><div class="label">Actions total</div></div>`+
      `<div class="card"><div class="num">${d.tweets_posted}</div><div class="label">Tweets</div></div>`+
      `<div class="card"><div class="num">${d.follows}</div><div class="label">Follows</div></div>`;
    let dh='<tr><th>Action</th><th>Agent</th><th>Prio</th></tr>';
    (d.recent_decisions||[]).slice().reverse().forEach(x=>{
      const pc=x.priority=='vert'?'vert':x.priority=='orange'?'orange':'rouge';
      dh+=`<tr><td>${(x.action||'').substring(0,50)}</td><td>${x.agent||''}</td><td class="${pc}">${x.priority||''}</td></tr>`;
    });
    document.getElementById('decisions').innerHTML=dh;
    let ah='<tr><th>Action</th><th>OK</th><th>Heure</th></tr>';
    (d.recent_actions||[]).slice().reverse().forEach(x=>{
      ah+=`<tr><td>${(x.action||'').substring(0,40)}</td><td class="${x.success?'ok':'fail'}">${x.success?'OK':'FAIL'}</td><td>${(x.ts||'').substring(11,19)}</td></tr>`;
    });
    document.getElementById('actions').innerHTML=ah;
    let au='<tr><th>Heure</th><th>Action</th><th>Prio</th><th>OK</th><th>Resultat</th></tr>';
    (d.audit||[]).forEach(x=>{
      const pc=x.priority=='vert'?'vert':x.priority=='orange'?'orange':'rouge';
      au+=`<tr><td>${(x.timestamp||'').substring(11,19)}</td><td>${(x.action||'').substring(0,30)}</td><td class="${pc}">${x.priority||''}</td><td class="${x.success?'ok':'fail'}">${x.success?'OK':'FAIL'}</td><td>${(x.result||'').substring(0,60)}</td></tr>`;
    });
    document.getElementById('audit').innerHTML=au;
    let rh='';
    (d.regles||[]).forEach((r,i)=>{rh+=`<div style="margin:3px 0">• ${r}</div>`;});
    document.getElementById('regles').innerHTML=rh||'<span style="color:#555">Aucune regle</span>';
    // Graphique 7 jours (barres CSS)
    let gh='<div style="display:flex;gap:8px;align-items:flex-end;height:100px">';
    const hist=d.daily_history||[];
    const maxA=Math.max(1,...hist.map(h=>h.actions));
    hist.forEach(h=>{
      const pct=Math.max(3,h.actions/maxA*100);
      gh+=`<div style="flex:1;text-align:center"><div style="background:linear-gradient(#00ff88,#008844);height:${pct}px;border-radius:3px 3px 0 0;margin:0 2px" title="${h.actions} actions"></div><div style="font-size:10px;color:#888;margin-top:3px">${h.date}</div><div style="font-size:11px;color:#00ff88">${h.actions}</div></div>`;
    });
    gh+='</div>';
    document.getElementById('chart').innerHTML=gh;

    // CRM
    const crm=d.crm||{};
    document.getElementById('crm').innerHTML=
      `<div>Contacts: <b>${crm.contacts||0}</b> | Follows: <b>${crm.follows||0}</b> | Groupes: <b>${crm.groups||0}</b></div>`+
      `<div style="color:#555;margin-top:4px">${(crm.groups_list||[]).map(g=>'• '+g.substring(0,40)).join('<br>')}</div>`;

    // Approvals
    const approvals=d.pending_approvals||[];
    if(approvals.length>0){
      let ah='';
      approvals.forEach(a=>{
        ah+=`<div style="margin:5px 0;padding:8px;background:#1a1a2a;border-radius:4px">
          <b class="orange">${a.priority}</b> ${a.action}
          <button class="btn btn-small" style="background:#00ff88;color:#000;margin-left:10px" onclick="approveAction('${a.id}',true)">Approve</button>
          <button class="btn btn-small" style="background:#ff4444;color:#fff;margin-left:5px" onclick="approveAction('${a.id}',false)">Deny</button>
        </div>`;
      });
      document.getElementById('approvals').innerHTML=ah;
    } else {
      document.getElementById('approvals').innerHTML='<span style="color:#555">Aucune action en attente</span>';
    }

    // ── Missions V9 today ──
    const mt=d.actions_today_by_type||{};
    const mtEntries=Object.entries(mt).filter(e=>e[1]>0).sort((a,b)=>b[1]-a[1]);
    let mth='';
    if(mtEntries.length===0){
      mth='<span style="color:#555">No missions run yet today</span>';
    }else{
      const maxM=Math.max(1,...mtEntries.map(e=>e[1]));
      mtEntries.forEach(function(e){
        const pct=Math.max(4,e[1]/maxM*100);
        mth+='<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'+
          '<div style="width:140px;color:#aaa">'+e[0]+'</div>'+
          '<div style="flex:1;background:#0a0a12;border-radius:3px;height:12px;overflow:hidden">'+
            '<div style="background:linear-gradient(#00ff88,#008844);height:12px;width:'+pct+'%"></div>'+
          '</div>'+
          '<div style="width:28px;text-align:right;color:#00ff88">'+e[1]+'</div>'+
        '</div>';
      });
    }
    document.getElementById('missions_today').innerHTML=mth;

    // ── Bridge Discord / Forum / Inbox ──
    const br=d.bridge||{};
    let brh='';
    if(!br.ok){
      brh='<div class="fail">VPS bridge offline: '+(br.error||'unknown')+'</div>';
    }else{
      const ct=br.counters||{};
      const parts=[];
      ['pending','processing','done','escalated','failed'].forEach(function(k){
        if(ct[k]!==undefined){
          const cls=k==='failed'?'rouge':(k==='escalated'?'orange':(k==='done'?'vert':'ok'));
          parts.push('<span style="margin-right:14px"><span style="color:#888">'+k+':</span> <b class="'+cls+'">'+ct[k]+'</b></span>');
        }
      });
      brh='<div style="margin-bottom:6px">'+(parts.join('')||'<span style="color:#555">no messages yet</span>')+'</div>'+
          '<div style="color:#555;font-size:11px">channels: '+((br.channels||[]).join(', ')||'-')+'</div>';
    }
    document.getElementById('bridge_status').innerHTML=brh;

    // ── MaxiaSalesAgent section ──
    renderSales(d.sales||{});

    document.getElementById('logs').textContent=(d.logs||[]).join('\\n');
    document.getElementById('logs').scrollTop=document.getElementById('logs').scrollHeight;
    document.getElementById('refresh').textContent='Mis a jour: '+new Date().toLocaleTimeString();
  }catch(e){document.getElementById('refresh').textContent='Erreur: '+e;}
}

function renderSales(s){
  if(!s||!s.ok){
    document.getElementById('sales_kpis').innerHTML='<div style="padding:10px;color:#555;font-size:12px">MaxiaSalesAgent not deployed yet (no conversations.db).</div>';
    document.getElementById('sales_stages').innerHTML='';
    document.getElementById('sales_channels').innerHTML='';
    document.getElementById('sales_langs').innerHTML='';
    document.getElementById('sales_latency').innerHTML='';
    document.getElementById('sales_active').innerHTML='';
    return;
  }
  const t=s.totals||{};
  document.getElementById('sales_kpis').innerHTML=
    '<div class="card"><div class="num">'+(t.all_time_conversations||0)+'</div><div class="label">All-time</div></div>'+
    '<div class="card"><div class="num">'+(t.active_24h||0)+'</div><div class="label">Active 24h</div></div>'+
    '<div class="card"><div class="num">'+(t.new_in_window||0)+'</div><div class="label">New 7d</div></div>'+
    '<div class="card"><div class="num">'+(t.currently_in_closing||0)+'</div><div class="label">In closing</div></div>'+
    '<div class="card"><div class="num">'+(t.funnel_conversion_pct||0)+'%</div><div class="label">Funnel %</div></div>';

  const stages=s.stages||{};
  const stageEntries=Object.entries(stages);
  const maxStage=Math.max(1,...Object.values(stages));
  let sh='';
  stageEntries.forEach(function(e){
    const stage=e[0],count=e[1];
    const pct=Math.max(2,count/maxStage*100);
    const color=stage==='6_closing'?'linear-gradient(#ff8c00,#cc5500)':'linear-gradient(#00ff88,#008844)';
    sh+='<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:11px">'+
      '<div style="width:160px;color:#aaa">'+stage+'</div>'+
      '<div style="flex:1;background:#0a0a12;border-radius:3px;height:14px;overflow:hidden">'+
        '<div style="background:'+color+';height:14px;width:'+pct+'%"></div>'+
      '</div>'+
      '<div style="width:30px;text-align:right;color:#00ff88">'+count+'</div>'+
    '</div>';
  });
  document.getElementById('sales_stages').innerHTML=sh||'<span style="color:#555">No stages yet</span>';

  const chs=s.channels||{};
  const chEntries=Object.entries(chs);
  document.getElementById('sales_channels').innerHTML=
    chEntries.length?chEntries.map(function(e){return '<span style="color:#aaa;margin-right:12px">'+e[0]+': <b style="color:#00ff88">'+e[1]+'</b></span>';}).join(''):'<span style="color:#555">none</span>';

  const lgs=s.languages||{};
  const lgEntries=Object.entries(lgs);
  document.getElementById('sales_langs').innerHTML=
    lgEntries.length?lgEntries.map(function(e){return '<span style="color:#aaa;margin-right:12px">'+e[0]+': <b style="color:#00ff88">'+e[1]+'</b></span>';}).join(''):'<span style="color:#555">none</span>';

  const tw=s.turns_window||{};
  document.getElementById('sales_latency').innerHTML=
    '<span style="color:#aaa;margin-right:12px">p50: <b style="color:#00ff88">'+(tw.latency_p50_s||0)+'s</b></span>'+
    '<span style="color:#aaa;margin-right:12px">p95: <b style="color:#00ff88">'+(tw.latency_p95_s||0)+'s</b></span>'+
    '<span style="color:#aaa;margin-right:12px">mean: <b style="color:#00ff88">'+(tw.latency_mean_s||0)+'s</b></span>'+
    '<span style="color:#aaa;margin-right:12px">bot turns: <b style="color:#00ff88">'+(tw.bot_turns||0)+'</b></span>'+
    '<span style="color:#aaa">tokens out: <b style="color:#00ff88">'+(tw.total_tokens_out||0)+'</b></span>';

  const active=s.active_list||[];
  let al='';
  active.forEach(function(c){
    const stagecls=c.stage==='6_closing'?'orange':(c.stage==='5_objection_handling'?'orange':'vert');
    const safeId=(c.id||'').replace(/'/g,"&#39;");
    al+='<div style="margin:4px 0;padding:8px;background:#0a0a12;border-radius:4px;cursor:pointer;border:1px solid #1a1a2a" onclick="showConversation(\\''+safeId+'\\')">'+
      '<div style="display:flex;justify-content:space-between;align-items:center">'+
        '<div><span class="'+stagecls+'">['+c.stage+']</span> <span style="color:#aaa">'+c.channel+' · '+c.lang+'</span></div>'+
        '<div style="color:#555;font-size:10px">'+(c.user_id||'').substring(0,40)+'</div>'+
      '</div>'+
      '<div style="color:#888;margin-top:4px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+(c.preview||'').replace(/[<>]/g,'')+'</div>'+
    '</div>';
  });
  document.getElementById('sales_active').innerHTML=al||'<span style="color:#555">No active conversations yet</span>';
}

async function showConversation(id){
  try{
    const r=await fetch('/api/sales/conversation?id='+encodeURIComponent(id));
    const c=await r.json();
    if(!c.ok){
      document.getElementById('convModalBody').innerHTML='<div class="fail">Error: '+(c.error||'unknown')+'</div>';
      document.getElementById('convModalTitle').textContent='Conversation '+id;
      document.getElementById('convModalMeta').textContent='';
      document.getElementById('convModal').style.display='flex';
      return;
    }
    document.getElementById('convModalTitle').textContent='Conversation '+(c.user_id||c.conversation_id);
    const created=c.created_at?new Date(c.created_at*1000).toLocaleString():'?';
    const seen=c.last_seen_at?new Date(c.last_seen_at*1000).toLocaleString():'?';
    document.getElementById('convModalMeta').textContent=
      'Channel: '+c.channel+' · lang: '+c.lang+' · stage: '+c.stage+
      ' · created: '+created+' · last seen: '+seen+
      ' · turns: '+(c.history||[]).length;

    const lats={};
    (c.telemetry||[]).forEach(function(t){if(t.role==='bot')lats[t.turn_idx]=t.latency_ms;});

    let body='';
    if(c.summary){
      body+='<div style="background:#1a1a2a;padding:8px;border-radius:4px;margin-bottom:10px;border-left:3px solid #00ff88">'+
        '<div style="color:#00ff88;font-size:11px;margin-bottom:4px">Earlier turns summary</div>'+
        '<div style="color:#ccc">'+c.summary.replace(/[<>]/g,'')+'</div>'+
      '</div>';
    }
    (c.history||[]).forEach(function(t,idx){
      const isUser=t.role==='user';
      const align=isUser?'flex-end':'flex-start';
      const bg=isUser?'#1e2a3e':'#1a2e1a';
      const border=isUser?'#3a5a7a':'#2a5a2a';
      const label=isUser?'PROSPECT':'BOT';
      const lat=lats[idx]?(' · '+(lats[idx]/1000).toFixed(1)+'s'):'';
      body+='<div style="display:flex;justify-content:'+align+';margin:8px 0">'+
        '<div style="max-width:80%;background:'+bg+';border:1px solid '+border+';padding:8px 12px;border-radius:8px">'+
          '<div style="color:#00ff88;font-size:10px;margin-bottom:4px">'+label+' · '+(t.stage||'?')+lat+'</div>'+
          '<div style="color:#e0e0e0;white-space:pre-wrap">'+(t.content||'').replace(/[<>]/g,'')+'</div>'+
        '</div>'+
      '</div>';
    });
    document.getElementById('convModalBody').innerHTML=body||'<div style="color:#555">Empty history</div>';
    document.getElementById('convModal').style.display='flex';
  }catch(e){
    document.getElementById('convModalBody').innerHTML='<div class="fail">Fetch error: '+e+'</div>';
    document.getElementById('convModal').style.display='flex';
  }
}

function closeConvModal(){
  document.getElementById('convModal').style.display='none';
}
async function togglePause(){
  await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'toggle_pause'})});
  load();
}
async function updateInterval(v){
  await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'set_interval',value:parseInt(v)})});
}
async function addRule(){
  const r=document.getElementById('newRule').value;
  if(!r)return;
  await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add_rule',value:r})});
  document.getElementById('newRule').value='';
  load();
}
async function clearMemory(){
  if(!confirm('Reset toute la memoire du CEO local ?'))return;
  await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'clear_memory'})});
  load();
}
async function approveAction(id,approved){
  await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'approve',value:id,approved:approved})});
  load();
}
async function exportCSV(){
  const r=await fetch('/api/export-csv');
  const b=await r.blob();
  const url=URL.createObjectURL(b);
  const a=document.createElement('a');
  a.href=url;a.download='ceo_audit.csv';a.click();
}
async function reindexRag(){
  const btn=document.getElementById('reindexBtn');
  const orig=btn.textContent;
  btn.disabled=true;btn.textContent='Reindexing...';
  try{
    const r=await fetch('/api/rag/reindex',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});
    const d=await r.json();
    if(d.error){alert('Reindex failed: '+d.error);}
    else if(d.ran===false){alert('No change detected. Reason: '+(d.reason||'unknown'));}
    else{const s=d.stats||{};alert('Reindex OK\n+'+(s.chunks_added||0)+' chunks\n'+(s.files||0)+' files\n'+(s.skipped||0)+' skipped\n'+((s.elapsed_s||0).toFixed(1))+'s');}
  }catch(e){alert('Reindex error: '+e.message);}
  btn.disabled=false;btn.textContent=orig;
}
load();setInterval(load,10000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/dashboard":
            self._json_response(_get_dashboard_data())
        elif self.path == "/api/chat/alexis/history":
            self._json_response({"history": _load_alexis_chat()})
        elif self.path.startswith("/api/sales/conversation"):
            # GET /api/sales/conversation?id=email:foo@bar.com
            try:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                conv_id = (qs.get("id", [""])[0] or "").strip()
            except Exception:
                conv_id = ""
            if not conv_id:
                self._json_response({"ok": False, "error": "missing id"})
            else:
                self._json_response(_get_sales_conversation(conv_id))
        elif self.path == "/api/export-csv":
            rows = _read_audit(500)
            csv = "timestamp,action,agent,tier,priority,approved_by,success,result\n"
            for r in rows:
                csv += f"{r.get('timestamp','')},{r.get('action','')},{r.get('agent','')},{r.get('tier_used','')},{r.get('priority','')},{r.get('approved_by','')},{r.get('success','')},\"{r.get('result','')[:100]}\"\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=ceo_audit.csv")
            self.end_headers()
            self.wfile.write(csv.encode("utf-8"))
        elif self.path == "/" or self.path == "/index.html":
            # Inject data directly into HTML to avoid fetch issues
            data = _get_dashboard_data()
            data_json = json.dumps(data, default=str, ensure_ascii=False)
            html_with_data = _HTML.replace(
                "load();setInterval(load,10000);",
                f"var _initialData = {data_json};\n"
                "function loadFromData(d){\n"
                "  try{\n"
                "    isPaused=d.paused;\n"
                "    document.getElementById('status').className='status '+(isPaused?'status-pause':'status-run');\n"
                "    document.getElementById('status').textContent=isPaused?'PAUSE':'ACTIF';\n"
                "    document.getElementById('pauseBtn').textContent=isPaused?'Resume':'Pause';\n"
                "    document.getElementById('pauseBtn').className='btn '+(isPaused?'btn-resume':'btn-pause');\n"
                "    document.getElementById('interval').value=d.interval_s;\n"
                "    document.getElementById('kpis').innerHTML=\n"
                "      '<div class=\"card\"><div class=\"num\">'+d.cycle_count+'</div><div class=\"label\">Cycles</div></div>'+\n"
                "      '<div class=\"card\"><div class=\"num\">'+d.decisions_total+'</div><div class=\"label\">Decisions</div></div>'+\n"
                "      '<div class=\"card\"><div class=\"num\">'+d.actions_today+'</div><div class=\"label\">Actions today</div></div>'+\n"
                "      '<div class=\"card\"><div class=\"num\">'+d.actions_total+'</div><div class=\"label\">Actions total</div></div>'+\n"
                "      '<div class=\"card\"><div class=\"num\">'+d.tweets_posted+'</div><div class=\"label\">Tweets</div></div>'+\n"
                "      '<div class=\"card\"><div class=\"num\">'+(d.follows||0)+'</div><div class=\"label\">Follows</div></div>';\n"
                "    document.getElementById('refresh').textContent='Loaded: '+new Date().toLocaleTimeString();\n"
                "  }catch(e){document.getElementById('refresh').textContent='Error: '+e;}\n"
                "}\n"
                "loadFromData(_initialData);\n"
                "load();setInterval(load,10000);"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_with_data.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/chat/alexis":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                self._json_response({"error": "invalid JSON"})
                return
            msg = (body.get("message", "") or "").strip()
            if not msg:
                self._json_response({"error": "empty message"})
                return
            result = _alexis_chat_reply(msg)
            self._json_response(result)
            return
        if self.path == "/api/chat/alexis/clear":
            _save_alexis_chat([])
            self._json_response({"cleared": True})
            return
        if self.path == "/api/rag/reindex":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                body = {}
            force = bool(body.get("force", True))
            try:
                import sys as _sys_rag
                if str(_DIR) not in _sys_rag.path:
                    _sys_rag.path.insert(0, str(_DIR))
                from missions.reindex_rag import mission_reindex_rag
                import asyncio as _asyncio
                result = _asyncio.run(mission_reindex_rag(force=force))
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e), "trace": traceback.format_exc()[-500:]})
            return
        if self.path == "/api/control":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            action = body.get("action", "")
            value = body.get("value", "")

            ctrl = _load_control()
            mem = _load_memory()

            if action == "toggle_pause":
                ctrl["paused"] = not ctrl.get("paused", False)
                _save_control(ctrl)
                self._json_response({"paused": ctrl["paused"]})
            elif action == "set_interval":
                ctrl["interval_s"] = max(60, min(3600, int(value)))
                _save_control(ctrl)
                self._json_response({"interval_s": ctrl["interval_s"]})
            elif action == "add_rule":
                mem.setdefault("regles", []).append(str(value)[:200])
                _save_memory(mem)
                self._json_response({"regles": len(mem["regles"])})
            elif action == "approve":
                try:
                    from notifier import approve_action
                    approved = body.get("approved", True)
                    approve_action(str(value), approved)
                    self._json_response({"approved": approved, "id": value})
                except Exception as e:
                    self._json_response({"error": str(e)})
                return
            elif action == "clear_memory":
                _save_memory({"decisions": [], "actions_done": [], "regles": [],
                              "tweets_posted": [], "contacts": [], "follows": [],
                              "cycle_count": 0, "daily_stats": {}})
                self._json_response({"cleared": True})
            else:
                self._json_response({"error": f"Unknown action: {action}"})
        else:
            self.send_response(404)
            self.end_headers()

    def _json_response(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import sys as _sys_main
    # Preflight: warn if this Python can't reach the CEO local LLM stack.
    # Alexis' PC has two Python 3.12 installs — the Windows Store one lacks
    # MAXIA deps. The correct interpreter is the one that runs ceo_main.py.
    try:
        import httpx  # noqa: F401
        print(f"[Dashboard] httpx OK — interpreter: {_sys_main.executable}")
    except ImportError:
        _windows_store = (
            "WindowsApps" in _sys_main.executable
            or "PythonSoftwareFoundation" in _sys_main.executable
        )
        print(
            "[Dashboard] WARNING: httpx not installed in this Python.\n"
            "  The chat section will NOT be able to reach the CEO LLM.\n"
            f"  Current interpreter: {_sys_main.executable}\n"
        )
        if _windows_store:
            print(
                "  This looks like the Windows Store Python.\n"
                "  Use the installer Python instead — try:\n"
                "    start_dashboard.bat  (in this folder)\n"
                "  or manually:\n"
                "    \"C:\\Users\\Mini pc\\AppData\\Local\\Programs\\Python\\Python312\\python.exe\" "
                f"\"{__file__}\""
            )
        else:
            print(
                "  Install httpx in this interpreter:\n"
                f"    \"{_sys_main.executable}\" -m pip install httpx requests\n"
                "  or relaunch via start_dashboard.bat to use the correct Python."
            )
    print("[Dashboard] http://localhost:8888")
    server = ThreadingHTTPServer(("127.0.0.1", 8888), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[Dashboard] Arret")
        server.server_close()
