"""Ed25519 identity for MAXIA × AgentMesh integration.

Mirrors the same primitives used by MAXIA's ``agent_permissions`` module
and LlamaIndex ``AgentMesh``: PyNaCl ed25519 keypair + base58 encoding
+ W3C DID. This gives us a single identity that both sides recognize.

Usage::

    identity = MaxiaMeshIdentity.generate(agent_name="reviewer")
    # or
    identity = MaxiaMeshIdentity.from_env()  # reads MAXIA_MESH_KEY_HEX
    # or
    identity = MaxiaMeshIdentity(
        did="did:web:me.com:agent:x",
        private_key_hex="...",
    )

    sig = identity.sign(b"some-canonical-payload")
    ok = identity.verify_self(b"some-canonical-payload", sig)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import base58
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError


_DEFAULT_DID_NAMESPACE = "did:web:maxiaworld.app:agent"


@dataclass
class MaxiaMeshIdentity:
    """An ed25519 identity bound to a W3C DID.

    The ``private_key_hex`` is the ONLY secret you must keep. Everything
    else (did, public key) is derivable from it. Never commit the private
    key to source control — use an env var or KMS.

    Attributes
    ----------
    did:
        W3C DID. Defaults to ``did:web:maxiaworld.app:agent:{name}`` if
        generated via :meth:`generate`. Can be overridden if your agent
        is hosted elsewhere.
    private_key_hex:
        64-char hex of the ed25519 private key (32 bytes seed).
    agent_name:
        Short slug used for DID autogen + logs. Optional.
    """

    did: str
    private_key_hex: str
    agent_name: str = ""

    # Lazy attributes populated from private_key_hex on first access
    _signing_key: Optional[SigningKey] = field(default=None, repr=False)
    _public_key_b58: Optional[str] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def generate(cls, agent_name: str = "agent") -> "MaxiaMeshIdentity":
        """Generate a brand-new ed25519 keypair + DID.

        The agent name is sanitized to a DNS-safe slug and used as the
        DID suffix. ``agent_name="My Bot"`` → ``did:web:maxiaworld.app:agent:my-bot``.
        """
        slug = _slugify(agent_name or "agent")
        sk = SigningKey.generate()
        return cls(
            did=f"{_DEFAULT_DID_NAMESPACE}:{slug}",
            private_key_hex=sk.encode().hex(),
            agent_name=slug,
        )

    @classmethod
    def from_env(
        cls,
        key_var: str = "MAXIA_MESH_KEY_HEX",
        did_var: str = "MAXIA_MESH_DID",
    ) -> "MaxiaMeshIdentity":
        """Load an identity from environment variables.

        ``MAXIA_MESH_KEY_HEX`` is required. ``MAXIA_MESH_DID`` is
        optional — if absent, a default is synthesised from the public
        key fingerprint.
        """
        key_hex = os.environ.get(key_var, "").strip()
        if not key_hex:
            raise ValueError(
                f"Environment variable {key_var} is empty or missing"
            )
        sk = SigningKey(bytes.fromhex(key_hex))
        pub_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
        did = os.environ.get(did_var, "").strip() or (
            f"{_DEFAULT_DID_NAMESPACE}:{pub_b58[:12].lower()}"
        )
        return cls(did=did, private_key_hex=key_hex, agent_name=did.split(":")[-1])

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def signing_key(self) -> SigningKey:
        if self._signing_key is None:
            self._signing_key = SigningKey(bytes.fromhex(self.private_key_hex))
        return self._signing_key

    @property
    def verify_key(self) -> VerifyKey:
        return self.signing_key.verify_key

    @property
    def public_key_b58(self) -> str:
        if self._public_key_b58 is None:
            self._public_key_b58 = base58.b58encode(bytes(self.verify_key)).decode()
        return self._public_key_b58

    # ------------------------------------------------------------------
    # Signing / verification
    # ------------------------------------------------------------------

    def sign(self, payload: bytes) -> str:
        """Sign a byte payload. Returns base58-encoded 64-byte signature."""
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload must be bytes")
        signed = self.signing_key.sign(bytes(payload))
        return base58.b58encode(signed.signature).decode()

    def verify_self(self, payload: bytes, signature_b58: str) -> bool:
        """Verify that a signature matches our own public key."""
        try:
            sig = base58.b58decode(signature_b58)
            self.verify_key.verify(bytes(payload), sig)
            return True
        except (BadSignatureError, ValueError):
            return False

    @staticmethod
    def verify_peer(
        peer_pubkey_b58: str,
        payload: bytes,
        signature_b58: str,
    ) -> bool:
        """Verify a signature against any peer's public key."""
        try:
            pk_bytes = base58.b58decode(peer_pubkey_b58)
            sig = base58.b58decode(signature_b58)
            VerifyKey(pk_bytes).verify(bytes(payload), sig)
            return True
        except (BadSignatureError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Canonical payload helpers
    # ------------------------------------------------------------------

    def canonical_register(self, nonce: str, timestamp: int) -> bytes:
        """Canonical payload for the ``mesh/register`` signature.

        Format::

            maxia-mesh-register-v1|<did>|<nonce>|<timestamp>
        """
        return f"maxia-mesh-register-v1|{self.did}|{nonce}|{timestamp}".encode()

    def canonical_execute(
        self,
        skill_id: str,
        nonce: str,
        timestamp: int,
    ) -> bytes:
        """Canonical payload for the ``mesh/execute`` signature.

        Format::

            maxia-mesh-execute-v1|<did>|<skill_id>|<nonce>|<timestamp>
        """
        return (
            f"maxia-mesh-execute-v1|{self.did}|{skill_id}|{nonce}|{timestamp}"
        ).encode()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_public_dict(self) -> dict:
        """Public-facing dict safe to share (no private key)."""
        return {
            "did": self.did,
            "public_key": self.public_key_b58,
            "agent_name": self.agent_name,
        }


def _slugify(value: str) -> str:
    """Normalize a free-text agent name to a DNS-safe slug."""
    import re
    out = re.sub(r"[^a-zA-Z0-9-]+", "-", (value or "").lower()).strip("-")
    return out or "agent"
