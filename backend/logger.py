"""MAXIA Structured Logging — JSON formatter + file rotation for production"""
import logging, sys, json, time, os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log["error"] = self.formatException(record.exc_info)
        return json.dumps(log)


# Log directory
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        fmt = JSONFormatter()
        # Console handler
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        logger.addHandler(console)
        # File handler with rotation (5MB, 5 backups)
        try:
            file_handler = RotatingFileHandler(
                _LOG_DIR / "maxia.log",
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except Exception as e:
            logging.getLogger(__name__).error(f"File handler error: {e}")
        logger.setLevel(logging.INFO)
    return logger


# Convenience: app-wide logger
app_logger = get_logger("maxia")


def log_json(level: str, module: str, msg: str, **extra):
    """Ecrit un log structure en JSON sur stderr. Usage simple sans configurer un logger."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "level": level,
        "module": module,
        "msg": msg,
    }
    entry.update(extra)
    logging.getLogger(module).log(getattr(logging, level.upper(), logging.INFO), json.dumps(entry, default=str))
