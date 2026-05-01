"""Conftest global pour MAXIA backend tests."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-key-minimum-32-characters-long")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("DATABASE_URL", "")  # Force SQLite in tests
