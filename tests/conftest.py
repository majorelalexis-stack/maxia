"""MAXIA test configuration — set env vars before any backend import."""
import os

# Set required env vars BEFORE importing backend modules
os.environ.setdefault("JWT_SECRET", "test-secret-key-minimum-32-characters-long")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ADMIN_KEY", "test-admin-key-32chars-minimum!!")
