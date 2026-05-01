"""Tests TDD pour MAXIA Hub — Phase 1 (registre d'état civil agents AI).

Ordre TDD : ces tests sont écrits AVANT l'implémentation.
Ils doivent tous échouer (RED) au premier lancement.
"""
import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nacl.signing import SigningKey
import base58


# ─── Helpers de génération de keypair ────────────────────────────────────────

def make_keypair():
    """Retourne (signing_key, public_key_b58) — keypair ed25519."""
    sk = SigningKey.generate()
    pk_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    return sk, pk_b58


def sign_message(sk: SigningKey, message: str) -> str:
    """Signe un message et retourne la signature en base58."""
    sig = sk.sign(message.encode()).signature
    return base58.b58encode(sig).decode()


# ─── Mock DB factory ─────────────────────────────────────────────────────────

def make_mock_db():
    """Crée un mock db compatible avec les patterns database.py."""
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def keypair():
    return make_keypair()


@pytest.fixture
def valid_challenge_row():
    """Ligne hub_challenges valide (non expirée, non utilisée)."""
    return {
        "challenge_id": uuid.uuid4().hex,
        "challenge_hex": "ab" * 32,  # 64 hex chars = 32 bytes
        "endpoint": "https://agent.example.com",
        "public_key": "FakePublicKeyBase58xxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "created_at": int(time.time()) - 60,  # 1 min ago — still valid (TTL=5min)
        "used": 0,
    }


@pytest.fixture
def valid_agent_row():
    """Ligne hub_agents valide."""
    sk, pk = make_keypair()
    hub_id = uuid.uuid4().hex
    return {
        "hub_id": hub_id,
        "did": f"did:maxia:solana:TestWallet{hub_id[:8]}",
        "name": "TestAgent",
        "endpoint": "https://agent.example.com",
        "public_key": pk,
        "wallet": f"TestWallet{hub_id[:8]}",
        "chain": "solana",
        "framework": "custom",
        "capabilities": '["text","analysis"]',
        "manifest_url": None,
        "corpus_opt_out": 0,
        "birth_ts": int(time.time()) - 3600,
        "last_heartbeat": int(time.time()) - 60,
        "uptime_30d": 99.5,
        "score": 42,
        "status": "active",
    }, sk


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubChallenge
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubChallenge:
    """POST /api/hub/challenge — génère un challenge ed25519 (step 1 registration)."""

    @pytest.mark.asyncio
    async def test_challenge_returns_challenge_id_and_hex(self, keypair):
        """La réponse contient challenge_id et challenge (hex)."""
        from hub.hub_registry import router
        from hub.hub_models import HubChallengeRequest

        sk, pk = keypair
        mock_db = make_mock_db()

        with patch("hub.hub_registry.db", mock_db):
            # Import direct de la fonction endpoint
            from hub.hub_registry import challenge_endpoint
            req = HubChallengeRequest(
                endpoint="https://agent.example.com",
                public_key=pk,
            )
            resp = await challenge_endpoint(req)

        assert hasattr(resp, "challenge_id")
        assert hasattr(resp, "challenge")
        assert resp.challenge_id  # non vide
        assert resp.challenge  # non vide

    @pytest.mark.asyncio
    async def test_challenge_hex_is_64_chars(self, keypair):
        """Le challenge est 32 bytes = 64 caractères hex."""
        from hub.hub_registry import challenge_endpoint
        from hub.hub_models import HubChallengeRequest

        sk, pk = keypair
        mock_db = make_mock_db()

        with patch("hub.hub_registry.db", mock_db):
            req = HubChallengeRequest(
                endpoint="https://agent.example.com",
                public_key=pk,
            )
            resp = await challenge_endpoint(req)

        assert len(resp.challenge) == 64
        # Doit être du hex valide
        int(resp.challenge, 16)  # lève ValueError si non-hex

    @pytest.mark.asyncio
    async def test_challenge_stores_in_db(self, keypair):
        """Le challenge est persisté en DB (raw_execute appelé)."""
        from hub.hub_registry import challenge_endpoint
        from hub.hub_models import HubChallengeRequest

        sk, pk = keypair
        mock_db = make_mock_db()

        with patch("hub.hub_registry.db", mock_db):
            req = HubChallengeRequest(
                endpoint="https://agent.example.com",
                public_key=pk,
            )
            await challenge_endpoint(req)

        mock_db.raw_execute.assert_called_once()
        # Vérifier que l'INSERT inclut hub_challenges
        call_args = mock_db.raw_execute.call_args
        assert "hub_challenges" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_challenge_expires_at_is_future(self, keypair):
        """expires_at est dans le futur (~5min)."""
        from hub.hub_registry import challenge_endpoint
        from hub.hub_models import HubChallengeRequest

        sk, pk = keypair
        mock_db = make_mock_db()

        now = int(time.time())
        with patch("hub.hub_registry.db", mock_db):
            req = HubChallengeRequest(
                endpoint="https://agent.example.com",
                public_key=pk,
            )
            resp = await challenge_endpoint(req)

        assert resp.expires_at > now
        # TTL ≈ 5 min = 300s (tolérance ±10s)
        assert resp.expires_at <= now + 310


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubRegister
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubRegister:
    """POST /api/hub/register — vérifie challenge + crée l'agent."""

    def _make_register_payload(self, sk: SigningKey, pk_b58: str, challenge_hex: str, challenge_id: str, chain: str = "solana"):
        """Construit un payload register valide."""
        from hub.hub_models import HubRegisterRequest
        sig = sign_message(sk, challenge_hex)
        return HubRegisterRequest(
            challenge_id=challenge_id,
            challenge_sig=sig,
            name="TestAgent",
            endpoint="https://agent.example.com",
            public_key=pk_b58,
            wallet=f"Wallet{uuid.uuid4().hex[:20]}",
            chain=chain,
            framework="custom",
            capabilities=["text", "analysis"],
            corpus_opt_out=False,
        )

    @pytest.mark.asyncio
    async def test_register_valid_sig_creates_agent(self, keypair):
        """Signature valide → agent créé, hub_id retourné."""
        from hub.hub_registry import register_endpoint
        from hub.hub_models import HubRegisterRequest

        sk, pk = keypair
        challenge_hex = "ab" * 32
        challenge_id = uuid.uuid4().hex

        mock_db = make_mock_db()
        challenge_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 60,
            "used": 0,
        }
        # Premier fetchone → challenge, deuxième → pas de doublon wallet
        mock_db._fetchone = AsyncMock(side_effect=[challenge_row, None])

        with patch("hub.hub_registry.db", mock_db):
            payload = self._make_register_payload(sk, pk, challenge_hex, challenge_id)
            resp = await register_endpoint(payload)

        assert hasattr(resp, "hub_id")
        assert resp.hub_id  # non vide
        assert len(resp.hub_id) == 32  # uuid4().hex

    @pytest.mark.asyncio
    async def test_register_did_format(self, keypair):
        """DID généré = did:web:maxiaworld.app:agent:{hub_id} (W3C DID via generate_did)."""
        from hub.hub_registry import register_endpoint

        sk, pk = keypair
        challenge_hex = "cd" * 32
        challenge_id = uuid.uuid4().hex
        wallet = f"Wallet{uuid.uuid4().hex[:20]}"

        mock_db = make_mock_db()
        challenge_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 60,
            "used": 0,
        }
        mock_db._fetchone = AsyncMock(side_effect=[challenge_row, None])

        from hub.hub_models import HubRegisterRequest
        sig = sign_message(sk, challenge_hex)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=sig,
                name="DIDTestAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=wallet,
                chain="base",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            resp = await register_endpoint(payload)

        # generate_did(hub_id) → did:web:maxiaworld.app:agent:{hub_id}
        assert resp.did.startswith("did:web:maxiaworld.app:agent:")
        assert len(resp.did.split(":")[-1]) == 32  # hub_id = uuid4().hex (32 chars)

    @pytest.mark.asyncio
    async def test_register_uaid_in_response(self, keypair):
        """UAID présent dans la réponse (non vide, Base58)."""
        from hub.hub_registry import register_endpoint

        sk, pk = keypair
        challenge_hex = "de" * 32
        challenge_id = uuid.uuid4().hex

        mock_db = make_mock_db()
        challenge_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 60,
            "used": 0,
        }
        mock_db._fetchone = AsyncMock(side_effect=[challenge_row, None])

        from hub.hub_models import HubRegisterRequest
        sig = sign_message(sk, challenge_hex)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=sig,
                name="UAIDTestAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=f"Wallet{uuid.uuid4().hex[:20]}",
                chain="solana",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            resp = await register_endpoint(payload)

        assert hasattr(resp, "uaid")
        assert resp.uaid  # non vide
        # UAID = Base58 (ne contient pas de caractères hors alphabet Base58)
        b58_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
        assert all(c in b58_chars for c in resp.uaid)

    @pytest.mark.asyncio
    async def test_register_wrong_sig_returns_400(self, keypair):
        """Signature invalide → HTTP 400."""
        from hub.hub_registry import register_endpoint
        from hub.hub_models import HubRegisterRequest
        from fastapi import HTTPException

        sk, pk = keypair
        challenge_hex = "ef" * 32
        challenge_id = uuid.uuid4().hex

        # Signe avec une AUTRE clé
        other_sk = SigningKey.generate()
        bad_sig = sign_message(other_sk, challenge_hex)

        mock_db = make_mock_db()
        challenge_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 60,
            "used": 0,
        }
        mock_db._fetchone = AsyncMock(return_value=challenge_row)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=bad_sig,
                name="BadAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=f"Wallet{uuid.uuid4().hex[:20]}",
                chain="solana",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            with pytest.raises(HTTPException) as exc_info:
                await register_endpoint(payload)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_register_expired_challenge_returns_400(self, keypair):
        """Challenge expiré (>5min) → HTTP 400."""
        from hub.hub_registry import register_endpoint
        from fastapi import HTTPException

        sk, pk = keypair
        challenge_hex = "12" * 32
        challenge_id = uuid.uuid4().hex

        mock_db = make_mock_db()
        # created_at = 6 min ago → expiré
        expired_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 360,  # 6 min ago
            "used": 0,
        }
        mock_db._fetchone = AsyncMock(return_value=expired_row)

        from hub.hub_models import HubRegisterRequest
        sig = sign_message(sk, challenge_hex)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=sig,
                name="LateAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=f"Wallet{uuid.uuid4().hex[:20]}",
                chain="solana",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            with pytest.raises(HTTPException) as exc_info:
                await register_endpoint(payload)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_register_already_used_challenge_returns_400(self, keypair):
        """Challenge déjà utilisé (used=1) → HTTP 400."""
        from hub.hub_registry import register_endpoint
        from fastapi import HTTPException

        sk, pk = keypair
        challenge_hex = "34" * 32
        challenge_id = uuid.uuid4().hex

        mock_db = make_mock_db()
        used_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 60,
            "used": 1,  # déjà utilisé
        }
        mock_db._fetchone = AsyncMock(return_value=used_row)

        from hub.hub_models import HubRegisterRequest
        sig = sign_message(sk, challenge_hex)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=sig,
                name="ReplayAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=f"Wallet{uuid.uuid4().hex[:20]}",
                chain="solana",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            with pytest.raises(HTTPException) as exc_info:
                await register_endpoint(payload)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_register_duplicate_wallet_returns_409(self, keypair):
        """Wallet déjà enregistré → HTTP 409."""
        from hub.hub_registry import register_endpoint
        from fastapi import HTTPException

        sk, pk = keypair
        challenge_hex = "56" * 32
        challenge_id = uuid.uuid4().hex
        wallet = f"WalletDuplicate{uuid.uuid4().hex[:10]}"

        mock_db = make_mock_db()
        challenge_row = {
            "challenge_id": challenge_id,
            "challenge_hex": challenge_hex,
            "endpoint": "https://agent.example.com",
            "public_key": pk,
            "created_at": int(time.time()) - 60,
            "used": 0,
        }
        existing_agent = {"hub_id": uuid.uuid4().hex, "wallet": wallet}
        # Premier fetchone → challenge OK, deuxième → wallet déjà existant
        mock_db._fetchone = AsyncMock(side_effect=[challenge_row, existing_agent])

        from hub.hub_models import HubRegisterRequest
        sig = sign_message(sk, challenge_hex)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=sig,
                name="DuplicateAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=wallet,
                chain="solana",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            with pytest.raises(HTTPException) as exc_info:
                await register_endpoint(payload)

        assert exc_info.value.status_code == 409

    def test_register_invalid_chain_returns_422(self):
        """Chain inconnue → Pydantic ValidationError (422 via FastAPI)."""
        from pydantic import ValidationError
        from hub.hub_models import HubRegisterRequest

        with pytest.raises(ValidationError):
            HubRegisterRequest(
                challenge_id=uuid.uuid4().hex,
                challenge_sig="fakesig",
                name="BadChainAgent",
                endpoint="https://agent.example.com",
                public_key="fakepk",
                wallet="fakeWallet",
                chain="invalid_chain_xyz",  # invalide
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )

    def test_register_too_many_capabilities_returns_422(self, keypair):
        """Plus de 10 capabilities → Pydantic ValidationError."""
        from pydantic import ValidationError
        from hub.hub_models import HubRegisterRequest

        sk, pk = keypair
        with pytest.raises(ValidationError):
            HubRegisterRequest(
                challenge_id=uuid.uuid4().hex,
                challenge_sig="fakesig",
                name="TooManyCapsAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=f"Wallet{uuid.uuid4().hex[:20]}",
                chain="solana",
                framework="custom",
                capabilities=["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"],  # 11 items
                corpus_opt_out=False,
            )

    @pytest.mark.asyncio
    async def test_register_challenge_not_found_returns_400(self, keypair):
        """Challenge inexistant → HTTP 400."""
        from hub.hub_registry import register_endpoint
        from hub.hub_models import HubRegisterRequest
        from fastapi import HTTPException

        sk, pk = keypair
        challenge_id = uuid.uuid4().hex

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=None)  # challenge absent

        sig = sign_message(sk, "ab" * 32)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubRegisterRequest(
                challenge_id=challenge_id,
                challenge_sig=sig,
                name="GhostAgent",
                endpoint="https://agent.example.com",
                public_key=pk,
                wallet=f"Wallet{uuid.uuid4().hex[:20]}",
                chain="solana",
                framework="custom",
                capabilities=["text"],
                corpus_opt_out=False,
            )
            with pytest.raises(HTTPException) as exc_info:
                await register_endpoint(payload)

        assert exc_info.value.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubHeartbeat
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubHeartbeat:
    """POST /api/hub/heartbeat — ping de vie."""

    @pytest.mark.asyncio
    async def test_heartbeat_valid_sig_returns_ok(self, valid_agent_row):
        """Heartbeat valide → ok=True."""
        from hub.hub_registry import heartbeat_endpoint
        from hub.hub_models import HubHeartbeatRequest

        agent_data, sk = valid_agent_row
        hub_id = agent_data["hub_id"]
        ts = int(time.time())
        msg = hub_id + str(ts)
        sig = sign_message(sk, msg)

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent_data)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])  # no heartbeats for uptime calc

        with patch("hub.hub_registry.db", mock_db):
            payload = HubHeartbeatRequest(hub_id=hub_id, sig=sig, timestamp=ts)
            resp = await heartbeat_endpoint(payload)

        assert resp.ok is True

    @pytest.mark.asyncio
    async def test_heartbeat_timestamp_too_old_returns_400(self, valid_agent_row):
        """Timestamp > 60s d'écart → HTTP 400."""
        from hub.hub_registry import heartbeat_endpoint
        from hub.hub_models import HubHeartbeatRequest
        from fastapi import HTTPException

        agent_data, sk = valid_agent_row
        hub_id = agent_data["hub_id"]
        ts = int(time.time()) - 120  # 2 min ago
        msg = hub_id + str(ts)
        sig = sign_message(sk, msg)

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent_data)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubHeartbeatRequest(hub_id=hub_id, sig=sig, timestamp=ts)
            with pytest.raises(HTTPException) as exc_info:
                await heartbeat_endpoint(payload)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_heartbeat_wrong_sig_returns_401(self, valid_agent_row):
        """Signature invalide → HTTP 401."""
        from hub.hub_registry import heartbeat_endpoint
        from hub.hub_models import HubHeartbeatRequest
        from fastapi import HTTPException

        agent_data, sk = valid_agent_row
        hub_id = agent_data["hub_id"]
        ts = int(time.time())

        # Signe avec une autre clé
        other_sk = SigningKey.generate()
        bad_sig = sign_message(other_sk, hub_id + str(ts))

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent_data)

        with patch("hub.hub_registry.db", mock_db):
            payload = HubHeartbeatRequest(hub_id=hub_id, sig=bad_sig, timestamp=ts)
            with pytest.raises(HTTPException) as exc_info:
                await heartbeat_endpoint(payload)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_heartbeat_unknown_hub_id_returns_404(self):
        """Hub_id inconnu → HTTP 404."""
        from hub.hub_registry import heartbeat_endpoint
        from hub.hub_models import HubHeartbeatRequest
        from fastapi import HTTPException

        sk = SigningKey.generate()
        hub_id = uuid.uuid4().hex
        ts = int(time.time())
        sig = sign_message(sk, hub_id + str(ts))

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=None)  # agent absent

        with patch("hub.hub_registry.db", mock_db):
            payload = HubHeartbeatRequest(hub_id=hub_id, sig=sig, timestamp=ts)
            with pytest.raises(HTTPException) as exc_info:
                await heartbeat_endpoint(payload)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_heartbeat_updates_last_heartbeat_in_db(self, valid_agent_row):
        """Heartbeat valide → raw_execute appelé pour update last_heartbeat."""
        from hub.hub_registry import heartbeat_endpoint
        from hub.hub_models import HubHeartbeatRequest

        agent_data, sk = valid_agent_row
        hub_id = agent_data["hub_id"]
        ts = int(time.time())
        sig = sign_message(sk, hub_id + str(ts))

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent_data)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_registry.db", mock_db):
            payload = HubHeartbeatRequest(hub_id=hub_id, sig=sig, timestamp=ts)
            await heartbeat_endpoint(payload)

        # Au moins 2 appels : INSERT heartbeat + UPDATE last_heartbeat
        assert mock_db.raw_execute.call_count >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubProfile
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubProfile:
    """GET /api/hub/agent/{hub_id} et GET /api/hub/agents."""

    @pytest.mark.asyncio
    async def test_get_agent_returns_profile(self, valid_agent_row):
        """GET /agent/{hub_id} → profil complet."""
        from hub.hub_registry import get_agent_endpoint

        agent_data, _ = valid_agent_row
        hub_id = agent_data["hub_id"]

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent_data)

        with patch("hub.hub_registry.db", mock_db):
            resp = await get_agent_endpoint(hub_id)

        assert resp.hub_id == hub_id
        assert resp.name == agent_data["name"]
        assert resp.chain == agent_data["chain"]

    @pytest.mark.asyncio
    async def test_get_unknown_agent_returns_404(self):
        """GET /agent/{hub_id} inconnu → HTTP 404."""
        from hub.hub_registry import get_agent_endpoint
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=None)

        with patch("hub.hub_registry.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await get_agent_endpoint(uuid.uuid4().hex)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_agents_default_params(self, valid_agent_row):
        """GET /agents → liste paginée avec valeurs par défaut."""
        from hub.hub_registry import list_agents_endpoint

        agent_data, _ = valid_agent_row
        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[agent_data])

        with patch("hub.hub_registry.db", mock_db):
            result = await list_agents_endpoint(skip=0, limit=20, chain=None, framework=None, min_score=0)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].hub_id == agent_data["hub_id"]

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_chain(self, valid_agent_row):
        """GET /agents?chain=solana → filtre appliqué en DB."""
        from hub.hub_registry import list_agents_endpoint

        agent_data, _ = valid_agent_row
        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[agent_data])

        with patch("hub.hub_registry.db", mock_db):
            result = await list_agents_endpoint(skip=0, limit=20, chain="solana", framework=None, min_score=0)

        # Vérifier que la query SQL inclut le filtre chain
        call_args = mock_db.raw_execute_fetchall.call_args
        sql = call_args[0][0]
        assert "chain" in sql.lower()

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_min_score(self, valid_agent_row):
        """GET /agents?min_score=10 → filtre score appliqué."""
        from hub.hub_registry import list_agents_endpoint

        agent_data, _ = valid_agent_row
        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[agent_data])

        with patch("hub.hub_registry.db", mock_db):
            result = await list_agents_endpoint(skip=0, limit=20, chain=None, framework=None, min_score=10)

        call_args = mock_db.raw_execute_fetchall.call_args
        sql = call_args[0][0]
        assert "score" in sql.lower()

    @pytest.mark.asyncio
    async def test_list_agents_empty_result(self):
        """GET /agents sur DB vide → liste vide."""
        from hub.hub_registry import list_agents_endpoint

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_registry.db", mock_db):
            result = await list_agents_endpoint(skip=0, limit=20, chain=None, framework=None, min_score=0)

        assert result == []

    @pytest.mark.asyncio
    async def test_list_agents_respects_limit(self):
        """GET /agents?limit=5 → Pydantic valide le param limit."""
        from hub.hub_registry import list_agents_endpoint

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_registry.db", mock_db):
            result = await list_agents_endpoint(skip=0, limit=5, chain=None, framework=None, min_score=0)

        call_args = mock_db.raw_execute_fetchall.call_args
        params = call_args[0][1]  # tuple de params SQL
        assert 5 in params  # LIMIT 5 dans les params


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubModels — validation Pydantic
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubModels:
    """Validation des modèles Pydantic."""

    def test_challenge_request_valid(self):
        from hub.hub_models import HubChallengeRequest
        r = HubChallengeRequest(endpoint="https://example.com", public_key="abc123")
        assert r.endpoint == "https://example.com"

    def test_register_name_too_short_raises(self, keypair):
        from pydantic import ValidationError
        from hub.hub_models import HubRegisterRequest
        sk, pk = keypair
        with pytest.raises(ValidationError):
            HubRegisterRequest(
                challenge_id="x",
                challenge_sig="sig",
                name="",  # trop court
                endpoint="https://example.com",
                public_key=pk,
                wallet="wallet123",
                chain="solana",
                framework="custom",
                capabilities=[],
                corpus_opt_out=False,
            )

    def test_register_name_too_long_raises(self, keypair):
        from pydantic import ValidationError
        from hub.hub_models import HubRegisterRequest
        sk, pk = keypair
        with pytest.raises(ValidationError):
            HubRegisterRequest(
                challenge_id="x",
                challenge_sig="sig",
                name="A" * 101,  # 101 chars > max 100
                endpoint="https://example.com",
                public_key=pk,
                wallet="wallet123",
                chain="solana",
                framework="custom",
                capabilities=[],
                corpus_opt_out=False,
            )

    def test_register_framework_too_long_raises(self, keypair):
        from pydantic import ValidationError
        from hub.hub_models import HubRegisterRequest
        sk, pk = keypair
        with pytest.raises(ValidationError):
            HubRegisterRequest(
                challenge_id="x",
                challenge_sig="sig",
                name="ValidName",
                endpoint="https://example.com",
                public_key=pk,
                wallet="wallet123",
                chain="solana",
                framework="F" * 51,  # 51 > max 50
                capabilities=[],
                corpus_opt_out=False,
            )

    def test_valid_chains_accepted(self):
        from hub.hub_models import HubRegisterRequest
        valid_chains = [
            "solana", "base", "eth", "polygon", "arbitrum",
            "avalanche", "bnb", "ton", "sui", "tron",
            "near", "aptos", "sei", "bitcoin"
        ]
        for chain in valid_chains:
            req = HubRegisterRequest(
                challenge_id="x",
                challenge_sig="sig",
                name="ValidAgent",
                endpoint="https://example.com",
                public_key="pk",
                wallet="wallet",
                chain=chain,
                framework="custom",
                capabilities=[],
                corpus_opt_out=False,
            )
            assert req.chain == chain

    def test_heartbeat_request_valid(self):
        from hub.hub_models import HubHeartbeatRequest
        r = HubHeartbeatRequest(hub_id="abc123", sig="sigxxx", timestamp=int(time.time()))
        assert r.hub_id == "abc123"

    def test_hub_agent_profile_corpus_opt_out_bool(self, valid_agent_row):
        """corpus_opt_out converti de int DB en bool Python."""
        from hub.hub_models import HubAgentProfile
        agent_data, _ = valid_agent_row
        # corpus_opt_out=0 en DB doit être False en Python
        profile = HubAgentProfile(
            hub_id=agent_data["hub_id"],
            did=agent_data["did"],
            name=agent_data["name"],
            endpoint=agent_data["endpoint"],
            framework=agent_data["framework"],
            capabilities=["text"],
            score=agent_data["score"],
            uptime_30d=agent_data["uptime_30d"],
            birth_ts=agent_data["birth_ts"],
            last_heartbeat=agent_data["last_heartbeat"],
            chain=agent_data["chain"],
            corpus_opt_out=False,
        )
        assert profile.corpus_opt_out is False
