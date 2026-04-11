"""Mission 10 — Code Auditor.

Deep function-by-function audit of backend Python files.
Scans 1 function per day, sends results by email.
"""
import glob
import json
import logging
import os
import re
from datetime import datetime

from config_local import OLLAMA_MODEL
from llm import llm
from agents import CODE_AUDIT_SYSTEM_PROMPT
from scheduler import send_mail

log = logging.getLogger("ceo")

_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(__file__))  # local_ceo/
_BACKEND_DIR = os.path.normpath(os.path.join(_LOCAL_CEO_DIR, "..", "backend"))
_AUDIT_REPORT_FILE = os.path.join(_LOCAL_CEO_DIR, "audit_report.md")
_AUDIT_STATE_FILE = os.path.join(_LOCAL_CEO_DIR, "audit_state.json")


def _load_audit_state() -> dict:
    """Load audit progress (which files have been scanned today)."""
    default = {"date": "", "files_done": [], "total_bugs": 0, "started_at": ""}
    try:
        if os.path.exists(_AUDIT_STATE_FILE):
            with open(_AUDIT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            today = datetime.now().strftime("%Y-%m-%d")
            if data.get("date") == today:
                return data
    except Exception:
        pass
    default["date"] = datetime.now().strftime("%Y-%m-%d")
    return default


def _save_audit_state(state: dict) -> None:
    try:
        with open(_AUDIT_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2))
    except Exception as e:
        log.error("[AUDIT] State save error: %s", e)


def _extract_functions(code: str) -> list:
    """Extract (name, start_line, end_line) for each top-level function/method."""
    functions = []
    lines = code.split("\n")
    func_pattern = re.compile(r"^(async\s+)?def\s+(\w+)\s*\(")
    for i, line in enumerate(lines):
        m = func_pattern.match(line)
        if m:
            functions.append({"name": m.group(2), "start": i})
    # Set end_line for each function (start of next function or EOF)
    for j in range(len(functions)):
        if j + 1 < len(functions):
            functions[j]["end"] = functions[j + 1]["start"] - 1
        else:
            functions[j]["end"] = len(lines) - 1
    return functions


def _get_imports_section(code: str) -> str:
    """Extract the import block at the top of a file (first N lines before first def/class)."""
    lines = code.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("def ") or line.startswith("async def ") or line.startswith("class "):
            return "\n".join(lines[:i])
    return "\n".join(lines[:50])


async def mission_code_audit(mem: dict, actions: dict) -> bool:
    """Audit UNE FONCTION en profondeur par jour (imports + function + callers).

    Returns True if audit complete (all functions done for current file), False otherwise.
    Strategy: 1 function/day, deep analysis with full context.
    """
    audit = _load_audit_state()

    # Lister tous les .py du backend
    all_py = sorted(glob.glob(os.path.join(_BACKEND_DIR, "*.py")))
    if not all_py:
        log.warning("[AUDIT] No .py files found in %s", _BACKEND_DIR)
        return True

    # Filtrer les fichiers deja scannes entierement
    done_set = set(audit.get("files_done", []))
    remaining = [f for f in all_py if os.path.basename(f) not in done_set]

    if not remaining:
        log.info("[AUDIT] Audit complet — %d fichiers scannes, %d bugs trouves",
                 len(done_set), audit.get("total_bugs", 0))
        return True

    # Prendre le fichier en cours
    target = remaining[0]
    filename = os.path.basename(target)

    # Lire le fichier
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    except Exception as e:
        log.error("[AUDIT] Cannot read %s: %s", filename, e)
        audit["files_done"].append(filename)
        _save_audit_state(audit)
        return False

    lines = code.split("\n")
    line_count = len(lines)

    # Skip fichiers trop petits
    if line_count < 10:
        log.info("[AUDIT] Skip %s (%d lines — too small)", filename, line_count)
        audit["files_done"].append(filename)
        _save_audit_state(audit)
        return False

    # Extraire les fonctions du fichier
    functions = _extract_functions(code)
    if not functions:
        log.info("[AUDIT] Skip %s (no functions found)", filename)
        audit["files_done"].append(filename)
        _save_audit_state(audit)
        return False

    # Trouver la prochaine fonction non auditee dans ce fichier
    funcs_done = set(audit.get("funcs_done_current", []))
    remaining_funcs = [f for f in functions if f"{filename}:{f['name']}" not in funcs_done]

    if not remaining_funcs:
        # Toutes les fonctions de ce fichier sont auditees → fichier termine
        log.info("[AUDIT] %s complete — all %d functions audited", filename, len(functions))
        audit["files_done"].append(filename)
        audit["funcs_done_current"] = []
        _save_audit_state(audit)
        return False

    # Prendre UNE seule fonction
    func = remaining_funcs[0]
    func_code = "\n".join(lines[func["start"]:func["end"] + 1])
    imports_section = _get_imports_section(code)

    log.info("[AUDIT] Deep scan: %s -> %s() (L%d-%d) [%d/%d funcs]",
             filename, func["name"], func["start"] + 1, func["end"] + 1,
             len(funcs_done) + 1, len(functions))

    # Construire le prompt avec contexte complet
    numbered_func = "\n".join(
        f"{func['start'] + 1 + i}: {l}" for i, l in enumerate(lines[func["start"]:func["end"] + 1])
    )

    prompt = (
        f"File: {filename}\n\n"
        f"== IMPORTS (top of file) ==\n```python\n{imports_section}\n```\n\n"
        f"== FUNCTION TO AUDIT: {func['name']}() (lines {func['start']+1}-{func['end']+1}) ==\n"
        f"```python\n{numbered_func}\n```"
    )

    # Limiter la taille
    if len(prompt) > 12000:
        prompt = prompt[:12000] + "\n... (truncated)"

    result = await llm(
        prompt,
        system=CODE_AUDIT_SYSTEM_PROMPT,
        max_tokens=500,
        timeout=600,
    )

    file_bugs = []
    if result:
        # Nettoyer le thinking Qwen3
        if "<think>" in result and "</think>" in result:
            result = result.split("</think>")[-1].strip()

        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("BUG|"):
                parts = line.split("|", 3)
                if len(parts) == 4:
                    file_bugs.append({
                        "severity": parts[1].strip(),
                        "line": parts[2].strip(),
                        "desc": parts[3].strip(),
                    })

    # Init le rapport si premier function
    report_file = os.path.normpath(_AUDIT_REPORT_FILE)
    if not audit.get("started_at"):
        audit["started_at"] = datetime.now().isoformat()
        header = (
            f"# MAXIA Code Audit Report (Deep — 1 function/day)\n"
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Model**: {OLLAMA_MODEL}\n"
            f"**Backend files**: {len(all_py)}\n"
            f"**Auditor**: CEO Local (Mission 10 v2)\n\n"
            f"---\n\n"
        )
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(header)

    # Append au rapport
    func_label = f"{filename}:{func['name']}()"
    with open(report_file, "a", encoding="utf-8") as f:
        if file_bugs:
            f.write(f"### {func_label} (L{func['start']+1}-{func['end']+1}) — {len(file_bugs)} bug(s)\n\n")
            for bug in file_bugs:
                icon = {"CRITICAL": "[!!!]", "HIGH": "[!!]", "MEDIUM": "[!]"}.get(bug["severity"], "[ ]")
                f.write(f"- {icon} **{bug['severity']}** L{bug['line']}: {bug['desc']}\n")
            f.write("\n")
        else:
            f.write(f"### {func_label} (L{func['start']+1}-{func['end']+1}) — CLEAN\n\n")

    # Update state — marquer cette fonction comme faite
    audit.setdefault("funcs_done_current", []).append(f"{filename}:{func['name']}")
    audit["total_bugs"] = audit.get("total_bugs", 0) + len(file_bugs)
    _save_audit_state(audit)

    log.info("[AUDIT] %s: %d bugs | Function %d/%d in %s",
             func_label, len(file_bugs), len(funcs_done) + 1, len(functions), filename)

    # Envoyer mail quotidien avec le resultat de cette fonction
    try:
        if file_bugs:
            bug_summary = "\n".join(
                f"  - {b['severity']} L{b['line']}: {b['desc']}" for b in file_bugs
            )
            mail_body = f"Audit deep: {func_label}\n\n{bug_summary}"
        else:
            mail_body = f"Audit deep: {func_label} — CLEAN (aucun bug)"
        await send_mail(
            f"[MAXIA CEO] Audit: {func_label} — {len(file_bugs)} bug(s)",
            mail_body,
        )
    except Exception as e:
        log.error("[AUDIT] Mail send error: %s", e)

    return False
