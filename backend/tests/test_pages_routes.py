"""TDD — Routes pages légales : /terms /privacy /legal /trust /cgu"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from routes.pages_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, follow_redirects=False)


class TestLegalPages:
    def test_terms_ok(self, client, tmp_path, monkeypatch):
        (tmp_path / "terms.html").write_text("<html>Terms</html>")
        monkeypatch.setattr("routes.pages_routes.FRONTEND_DIR", tmp_path)
        r = client.get("/terms")
        assert r.status_code == 200

    def test_privacy_ok(self, client, tmp_path, monkeypatch):
        (tmp_path / "privacy.html").write_text("<html>Privacy</html>")
        monkeypatch.setattr("routes.pages_routes.FRONTEND_DIR", tmp_path)
        r = client.get("/privacy")
        assert r.status_code == 200

    def test_legal_ok(self, client, tmp_path, monkeypatch):
        (tmp_path / "legal.html").write_text("<html>Legal</html>")
        monkeypatch.setattr("routes.pages_routes.FRONTEND_DIR", tmp_path)
        r = client.get("/legal")
        assert r.status_code == 200

    def test_trust_ok(self, client, tmp_path, monkeypatch):
        (tmp_path / "trust.html").write_text("<html>Trust</html>")
        monkeypatch.setattr("routes.pages_routes.FRONTEND_DIR", tmp_path)
        r = client.get("/trust")
        assert r.status_code == 200

    def test_cgu_redirects_to_terms(self, client):
        r = client.get("/cgu")
        assert r.status_code == 301
        assert "/terms" in r.headers["location"]

    def test_terms_missing_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("routes.pages_routes.FRONTEND_DIR", tmp_path)
        r = client.get("/terms")
        assert r.status_code == 404

    def test_privacy_missing_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("routes.pages_routes.FRONTEND_DIR", tmp_path)
        r = client.get("/privacy")
        assert r.status_code == 404
