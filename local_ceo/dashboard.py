"""Dashboard local CEO MAXIA — http://localhost:8888

Zero dependance externe. Controles: pause/resume CEO, approuver actions ORANGE.
"""
import json, os, time, sqlite3, urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

_DIR = Path(__file__).parent
_MEMORY_FILE = _DIR / "ceo_memory.json"
_AUDIT_DB = _DIR / "ceo_audit.db"
_LOG_FILE = _DIR / "ceo_local.log"
_CONTROL_FILE = _DIR / "ceo_control.json"  # pause/resume + settings


def _load_memory() -> dict:
    try:
        if _MEMORY_FILE.exists():
            return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
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
    ctrl = _load_control()
    today = time.strftime("%Y-%m-%d")
    actions_today = [a for a in mem.get("actions_done", []) if a.get("ts", "").startswith(today)]
    return {
        "cycle_count": mem.get("cycle_count", 0),
        "decisions_total": len(mem.get("decisions", [])),
        "actions_total": len(mem.get("actions_done", [])),
        "actions_today": len(actions_today),
        "recent_decisions": mem.get("decisions", [])[-10:],
        "recent_actions": mem.get("actions_done", [])[-10:],
        "audit": _read_audit(15),
        "logs": _read_log(40),
        "tweets_posted": len(mem.get("tweets_posted", [])),
        "follows": len(mem.get("follows", [])),
        "contacts": len(mem.get("contacts", [])),
        "paused": ctrl.get("paused", False),
        "interval_s": ctrl.get("interval_s", 600),
        "regles": mem.get("regles", [])[-5:],
        # Historique 7 jours pour graphiques
        "daily_history": _compute_daily_history(mem),
    }


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
    <button class="btn btn-small" style="background:#252535;color:#e0e0e0" onclick="exportCSV()">Export CSV</button>
    <button class="btn btn-danger btn-small" onclick="clearMemory()">Reset memoire</button>
  </div>
</h1>
<div class="grid" id="kpis"></div>

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
<div class="section"><h2>Decisions recentes</h2><table id="decisions"></table></div>
<div class="section"><h2>Actions executees</h2><table id="actions"></table></div>
</div>
<div class="section"><h2>Audit</h2><table id="audit"></table></div>
<div class="section"><h2>Regles actives</h2><div id="regles" style="font-size:12px;color:#aaa"></div></div>
<div class="section"><h2>Logs</h2><div class="log-box" id="logs"></div></div>
<div class="refresh" id="refresh"></div>
<script>
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

    // Regles
    let rh='';
    (d.regles||[]).forEach((r,i)=>{rh+=`<div style="margin:3px 0">• ${r}</div>`;});
    document.getElementById('regles').innerHTML=rh||'<span style="color:#555">Aucune regle</span>';

    document.getElementById('logs').textContent=(d.logs||[]).join('\\n');
    document.getElementById('logs').scrollTop=document.getElementById('logs').scrollHeight;
    document.getElementById('refresh').textContent='Mis a jour: '+new Date().toLocaleTimeString();
  }catch(e){document.getElementById('refresh').textContent='Erreur: '+e;}
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
async function exportCSV(){
  const r=await fetch('/api/export-csv');
  const b=await r.blob();
  const url=URL.createObjectURL(b);
  const a=document.createElement('a');
  a.href=url;a.download='ceo_audit.csv';a.click();
}
load();setInterval(load,10000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/dashboard":
            self._json_response(_get_dashboard_data())
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
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
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
    print("[Dashboard] http://localhost:8888")
    server = HTTPServer(("127.0.0.1", 8888), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[Dashboard] Arret")
        server.server_close()
