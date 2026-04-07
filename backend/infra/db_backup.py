"""MAXIA DB Backup — Automated SQLite/PG backup + offsite copy (PRO-I4)"""
import logging
import asyncio, shutil, time, os
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "maxia.db"
BACKUP_DIR = Path(__file__).parent.parent / "backups"
MAX_BACKUPS = 30  # keep last 30 backups


async def _backup_pg(db_url: str) -> dict:
    """Backup PostgreSQL using pg_dump with custom format (compressed)."""
    try:
        PG_BACKUP_DIR = Path(__file__).parent.parent / "backups" / "pg"
        PG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = PG_BACKUP_DIR / f"maxia_pg_{ts}.dump"
        # pg_dump with custom format (compressed, supports pg_restore)
        # Pass connection string via PGDATABASE env var to avoid leaking password in process args
        env = {**os.environ, "PGDATABASE": db_url}
        proc = await asyncio.create_subprocess_exec(
            "pg_dump", "--format=custom", "--no-owner",
            f"--file={dest}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode()[:200] if stderr else "unknown error"
            logger.error("[Backup] pg_dump failed: %s", err_msg)
            return {"success": False, "error": "pg_dump failed"}
        # Verify dump is not empty
        size_kb = dest.stat().st_size / 1024
        if size_kb < 1:
            logger.error("[Backup] pg_dump produced empty file")
            dest.unlink(missing_ok=True)
            return {"success": False, "error": "pg_dump produced empty file"}
        # Cleanup old PG backups (keep last 30)
        backups = sorted(PG_BACKUP_DIR.glob("maxia_pg_*.dump"), key=lambda p: p.stat().st_mtime)
        while len(backups) > MAX_BACKUPS:
            backups.pop(0).unlink()
        logger.info("[Backup] PostgreSQL saved: %s (%.0f KB)", dest.name, size_kb)
        return {"success": True, "file": str(dest), "size_kb": round(size_kb), "format": "pg_custom"}
    except FileNotFoundError:
        logger.warning("[Backup] pg_dump not found — install postgresql-client")
        return {"success": False, "error": "pg_dump not installed"}
    except Exception as e:
        logger.error("[Backup] PG backup error: %s", e)
        return {"success": False, "error": "An error occurred"}


async def backup_db():
    """Create a timestamped copy of maxia.db, or run pg_dump for PostgreSQL."""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url and db_url.startswith("postgresql"):
        return await _backup_pg(db_url)
    if not DB_PATH.exists():
        return {"success": True, "skipped": True, "reason": "No database file found"}
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
        logger.info(f"[Backup] DB saved: {dest.name} ({size_kb:.0f} KB)")
        return {"success": True, "file": str(dest), "size_kb": round(size_kb)}
    except Exception as e:
        logger.error(f"[Backup] Error: {e}")
        return {"success": False, "error": "An error occurred"}


async def run_backup_scheduler():
    """Backup every 6 hours + offsite copy."""
    while True:
        result = await backup_db()
        if result.get("success") and result.get("file"):
            await offsite_copy(result["file"])
        await asyncio.sleep(21600)  # 6 hours


# ══════════════════════════════════════════
# OFFSITE BACKUP (PRO-I4)
# ══════════════════════════════════════════

OFFSITE_DIR = Path(os.getenv("BACKUP_OFFSITE_DIR", ""))
OFFSITE_SCP_TARGET = os.getenv("BACKUP_OFFSITE_SCP", "")  # user@host:/path/


async def offsite_copy(backup_path: str) -> dict:
    """Copy latest backup to offsite location (local dir or SCP).

    Configure via env vars:
    - BACKUP_OFFSITE_DIR=/mnt/external/maxia-backups  (local/NFS mount)
    - BACKUP_OFFSITE_SCP=user@backup-server:/backups/maxia/  (remote SCP)
    """
    src = Path(backup_path)
    if not src.exists():
        return {"success": False, "error": "Source file not found"}

    # Method 1: Local/NFS directory copy
    if OFFSITE_DIR and OFFSITE_DIR != Path(""):
        try:
            OFFSITE_DIR.mkdir(parents=True, exist_ok=True)
            dest = OFFSITE_DIR / src.name
            shutil.copy2(str(src), str(dest))
            # Cleanup old offsite backups (keep 14)
            offsite_files = sorted(OFFSITE_DIR.glob("maxia_*"), key=lambda p: p.stat().st_mtime)
            while len(offsite_files) > 14:
                offsite_files.pop(0).unlink()
            size_kb = dest.stat().st_size / 1024
            logger.info("[Backup] Offsite copy: %s -> %s (%.0f KB)", src.name, OFFSITE_DIR, size_kb)
            return {"success": True, "method": "local", "dest": str(dest), "size_kb": round(size_kb)}
        except Exception as e:
            logger.error("[Backup] Offsite local copy failed: %s", e)
            return {"success": False, "error": str(e)[:200]}

    # Method 2: SCP to remote server
    if OFFSITE_SCP_TARGET:
        try:
            proc = await asyncio.create_subprocess_exec(
                "scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30",
                str(src), OFFSITE_SCP_TARGET,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                logger.info("[Backup] Offsite SCP: %s -> %s", src.name, OFFSITE_SCP_TARGET)
                return {"success": True, "method": "scp", "target": OFFSITE_SCP_TARGET}
            err = stderr.decode()[:200] if stderr else "unknown"
            logger.error("[Backup] Offsite SCP failed: %s", err)
            return {"success": False, "error": f"SCP failed: {err}"}
        except asyncio.TimeoutError:
            logger.error("[Backup] Offsite SCP timeout (120s)")
            return {"success": False, "error": "SCP timeout"}
        except FileNotFoundError:
            logger.warning("[Backup] scp not found — install openssh-client")
            return {"success": False, "error": "scp not installed"}
        except Exception as e:
            logger.error("[Backup] Offsite SCP error: %s", e)
            return {"success": False, "error": str(e)[:200]}

    # No offsite configured
    return {"success": False, "error": "No offsite target configured (set BACKUP_OFFSITE_DIR or BACKUP_OFFSITE_SCP)"}


def get_backup_list():
    """List available backups (SQLite + PostgreSQL)."""
    result = []
    # SQLite backups
    if BACKUP_DIR.exists():
        for b in sorted(BACKUP_DIR.glob("maxia_*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
            result.append({"file": b.name, "size_kb": round(b.stat().st_size / 1024), "date": time.ctime(b.stat().st_mtime), "type": "sqlite"})
    # PostgreSQL backups
    pg_dir = BACKUP_DIR / "pg"
    if pg_dir.exists():
        for b in sorted(pg_dir.glob("maxia_pg_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True):
            result.append({"file": b.name, "size_kb": round(b.stat().st_size / 1024), "date": time.ctime(b.stat().st_mtime), "type": "postgresql"})
    return result


async def restore_db(backup_name: str) -> dict:
    """Restore DB from a specific backup. Creates a safety backup of current DB first."""
    import re
    if not re.match(r'^maxia_[a-zA-Z0-9_]+\.db$', backup_name):
        return {"success": False, "error": "Invalid backup filename (must match maxia_*.db, no path chars)"}
    backup_file = BACKUP_DIR / backup_name
    if not backup_file.exists():
        return {"success": False, "error": "Invalid backup filename (must match maxia_*.db, no path chars)"}
    try:
        # Safety backup of current DB before restoring
        safety = BACKUP_DIR / f"maxia_pre_restore_{time.strftime('%Y%m%d_%H%M%S')}.db"
        if DB_PATH.exists():
            shutil.copy2(str(DB_PATH), str(safety))
            logger.info(f"[Backup] Safety backup: {safety.name}")
        # Restore
        shutil.copy2(str(backup_file), str(DB_PATH))
        size_kb = backup_file.stat().st_size / 1024
        logger.info(f"[Backup] RESTORED from {backup_name} ({size_kb:.0f} KB)")
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
