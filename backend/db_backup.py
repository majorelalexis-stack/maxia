"""MAXIA DB Backup — Automated SQLite backup"""
import logging
import asyncio, shutil, time, os
from pathlib import Path

DB_PATH = Path(__file__).parent / "maxia.db"
BACKUP_DIR = Path(__file__).parent / "backups"
MAX_BACKUPS = 30  # keep last 30 backups


async def backup_db():
    """Create a timestamped copy of maxia.db. Skips silently if using PostgreSQL (no .db file)."""
    if not DB_PATH.exists() or os.getenv("DATABASE_URL", ""):
        # PostgreSQL mode — SQLite backup not applicable (pg_dump cron handles PG backups)
        return {"success": True, "skipped": True, "reason": "PostgreSQL mode — no SQLite file"}
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"maxia_{ts}.db"
        shutil.copy2(str(DB_PATH), str(dest))
        # Cleanup old backups
        backups = sorted(BACKUP_DIR.glob("maxia_*.db"), key=lambda p: p.stat().st_mtime)
        while len(backups) > MAX_BACKUPS:
            backups.pop(0).unlink()
        size_kb = dest.stat().st_size / 1024
        print(f"[Backup] DB saved: {dest.name} ({size_kb:.0f} KB)")
        return {"success": True, "file": str(dest), "size_kb": round(size_kb)}
    except Exception as e:
        print(f"[Backup] Error: {e}")
        return {"success": False, "error": "An error occurred"}


async def run_backup_scheduler():
    """Backup every 6 hours."""
    while True:
        await backup_db()
        await asyncio.sleep(21600)  # 6 hours


def get_backup_list():
    """List available backups."""
    if not BACKUP_DIR.exists():
        return []
    backups = sorted(BACKUP_DIR.glob("maxia_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"file": b.name, "size_kb": round(b.stat().st_size / 1024), "date": time.ctime(b.stat().st_mtime)} for b in backups]


async def restore_db(backup_name: str) -> dict:
    """Restore DB from a specific backup. Creates a safety backup of current DB first."""
    backup_file = BACKUP_DIR / backup_name
    if not backup_file.exists():
        return {"success": False, "error": f"Backup not found: {backup_name}"}
    import re
    if not re.match(r'^maxia_[a-zA-Z0-9_]+\.db$', backup_name):
        return {"success": False, "error": "Invalid backup filename (must match maxia_*.db, no path chars)"}
    try:
        # Safety backup of current DB before restoring
        safety = BACKUP_DIR / f"maxia_pre_restore_{time.strftime('%Y%m%d_%H%M%S')}.db"
        if DB_PATH.exists():
            shutil.copy2(str(DB_PATH), str(safety))
            print(f"[Backup] Safety backup: {safety.name}")
        # Restore
        shutil.copy2(str(backup_file), str(DB_PATH))
        size_kb = backup_file.stat().st_size / 1024
        print(f"[Backup] RESTORED from {backup_name} ({size_kb:.0f} KB)")
        return {"success": True, "restored_from": backup_name, "size_kb": round(size_kb), "safety_backup": safety.name}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


async def verify_backup(backup_name: str) -> dict:
    """Verify that a backup is readable and has valid schema."""
    backup_file = BACKUP_DIR / backup_name
    if not backup_file.exists():
        return {"valid": False, "error": "File not found"}
    try:
        import aiosqlite
        async with aiosqlite.connect(str(backup_file)) as db:
            # Check tables exist
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in await cursor.fetchall()]
            # Count rows in key tables
            counts = {}
            for table in ["agents", "agent_services", "marketplace_tx"]:
                if table in tables:
                    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                    row = await cursor.fetchone()
                    counts[table] = row[0]
        return {"valid": True, "tables": len(tables), "rows": counts, "size_kb": round(backup_file.stat().st_size / 1024)}
    except Exception as e:
        return {"valid": False, "error": "An error occurred"}
