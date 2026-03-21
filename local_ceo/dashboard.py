"""Dashboard local CEO MAXIA — http://localhost:8888

Zero dependance externe (http.server natif Python).
Affiche : decisions, actions, couts, KPIs, logs, audit.
"""
import json, os, time, sqlite3
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

_DIR = Path(__file__).parent
_MEMORY_FILE = _DIR / "ceo_memory.json"
_AUDIT_DB = _DIR / "ceo_audit.db"
_LOG_FILE = _DIR / "ceo_local.log"


def _load_memory() -> dict:
    try:
        if _MEMORY_FILE.exists():
            return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _read_log(lines: int = 50) -> list:
    try:
        if _LOG_FILE.exists():
            all_lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            return all_lines[-lines:]
    except Exception:
        pass
    return []


def _read_audit(limit: int = 30) -> list:
    try:
        if _AUDIT_DB.exists():
            conn = sqlite3.connect(str(_AUDIT_DB))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM ceo_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
    except Exception:
        pass
    return []


def _get_dashboard_data() -> dict:
    mem = _load_memory()
    today = time.strftime("%Y-%m-%d")
    actions_today = [a for a in mem.get("actions_done", []) if a.get("ts", "").startswith(today)]
    return {
        "cycle_count": mem.get("cycle_count", 0),
        "decisions_total": len(mem.get("decisions", [])),
        "actions_total": len(mem.get("actions_done", [])),
        "actions_today": len(actions_today),
        "actions_today_detail": actions_today[-10:],
        "regles": mem.get("regles", []),
        "recent_decisions": mem.get("decisions", [])[-10:],
        "recent_actions": mem.get("actions_done", [])[-10:],
        "audit": _read_audit(15),
        "logs": _read_log(40),
        "tweets_posted": len(mem.get("tweets_posted", [])),
        "follows": len(mem.get("follows", [])),
        "contacts": len(mem.get("contacts", [])),
    }


_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CEO MAXIA Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0f;color:#e0e0e0;font-family:'Segoe UI',sans-serif;padding:20px}
h1{color:#00ff88;font-size:24px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:25px}
.card{background:#151520;border:1px solid #252535;border-radius:10px;padding:16px;text-align:center}
.card .num{font-size:32px;font-weight:bold;color:#00ff88}
.card .label{font-size:12px;color:#888;margin-top:4px}
.section{background:#151520;border:1px solid #252535;border-radius:10px;padding:16px;margin-bottom:18px}
.section h2{color:#00ff88;font-size:15px;margin-bottom:10px;border-bottom:1px solid #252535;padding-bottom:6px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#888;padding:5px 6px;border-bottom:1px solid #252535}
td{padding:5px 6px;border-bottom:1px solid #1a1a2a;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vert{color:#00ff88}.orange{color:#ff8c00}.rouge{color:#ff4444}
.ok{color:#00ff88}.fail{color:#ff4444}
.log-box{background:#0a0a12;border-radius:6px;padding:10px;font-family:monospace;font-size:11px;max-height:250px;overflow-y:auto;white-space:pre-wrap;line-height:1.5}
.refresh{color:#555;font-size:11px;text-align:right;margin-top:5px}
</style>
</head>
<body>
<h1>CEO MAXIA Dashboard</h1>
<div class="grid" id="kpis"></div>
<div class="cols">
<div class="section"><h2>Decisions recentes</h2><table id="decisions"></table></div>
<div class="section"><h2>Actions executees</h2><table id="actions"></table></div>
</div>
<div class="section"><h2>Audit</h2><table id="audit"></table></div>
<div class="section"><h2>Logs</h2><div class="log-box" id="logs"></div></div>
<div class="refresh" id="refresh"></div>
<script>
async function load(){
  try{
    const r=await fetch('/api/dashboard');
    const d=await r.json();
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
    document.getElementById('logs').textContent=(d.logs||[]).join('\\n');
    document.getElementById('logs').scrollTop=document.getElementById('logs').scrollHeight;
    document.getElementById('refresh').textContent='Mis a jour: '+new Date().toLocaleTimeString()+' (auto-refresh 15s)';
  }catch(e){document.getElementById('refresh').textContent='Erreur: '+e;}
}
load();setInterval(load,15000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/dashboard":
            data = _get_dashboard_data()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str, ensure_ascii=False).encode("utf-8"))
        elif self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Silence les logs HTTP


if __name__ == "__main__":
    print("[Dashboard] http://localhost:8888")
    server = HTTPServer(("127.0.0.1", 8888), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[Dashboard] Arret")
        server.server_close()
