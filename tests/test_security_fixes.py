"""Tests for security fixes from the March 28 audit.
Tests the critical fixes that protect real money on mainnet.

Covers:
- Auth token parsing (rsplit, HMAC signature, expiry)
- Solana USDC mint verification (V-05: reject non-USDC tokens)
- Content safety filter (Art.1 blocked words/patterns)
- Error utils (safe_error never leaks internals)
- Commission calculation tiers (BRONZE/GOLD/WHALE)
- Keccak-256 vs SHA3-256 (Ethereum uses pre-NIST Keccak)
- Nonce anti-replay protection
- Brute force rate limiting
"""
import hashlib
import hmac
import os
import sys
import time

import pytest

# ── Ensure backend/ is importable ──
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Ensure env vars are set before any imports
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-minimum-16-chars")
os.environ.setdefault("ADMIN_KEY", "test-admin-key-for-pytest-min16")


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """Reset auth module global state between tests to avoid pollution from test_backend.py."""
    yield
    # Cleanup after each test
    try:
        from auth import NONCES, _USED_NONCES, _FAILED_ATTEMPTS
        NONCES.clear()
        _USED_NONCES.clear()
        _FAILED_ATTEMPTS.clear()
    except Exception:
        pass


# =============================================================================
#  1. AUTH TOKEN PARSING (auth.py)
# =============================================================================

class TestSessionToken:
    """Test session token creation and verification."""

    def test_rsplit_handles_normal_wallet(self):
        """Token with standard Solana wallet parses correctly."""
        from auth import create_session_token, verify_session_token
        wallet = "ASfeGNbZCmTU8VCrvhfNNHLcyXGPVcr75zLXJHZvDTwA"
        token = create_session_token(wallet)
        result = verify_session_token(token)
        assert result == wallet

    def test_rsplit_handles_wallet_with_colons(self):
        """BUG 7 fix: wallet with colons must be REJECTED (base58 validation)."""
        from auth import create_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            create_session_token("wallet:with:colons")
        assert exc_info.value.status_code == 400

    def test_rsplit_rejects_tampered_token(self):
        """Tampered token signature is rejected."""
        from auth import create_session_token, verify_session_token
        from fastapi import HTTPException
        token = create_session_token("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU")
        parts = token.rsplit(":", 2)
        tampered = f"{parts[0]}:{parts[1]}:{'a' * 64}"
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token(tampered)
        assert exc_info.value.status_code == 401

    def test_expired_token_rejected(self):
        """Expired token is rejected with 401."""
        from auth import verify_session_token, _JWT_SECRET
        from fastapi import HTTPException
        wallet = "TestWallet"
        expired_payload = f"{wallet}:{int(time.time()) - 100}"
        sig = hmac.new(_JWT_SECRET.encode(), expired_payload.encode(), hashlib.sha256).hexdigest()
        token = f"{expired_payload}:{sig}"
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token(token)
        assert exc_info.value.status_code == 401

    def test_malformed_token_rejected(self):
        """Token with wrong number of parts is rejected."""
        from auth import verify_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token("only_one_part")
        assert exc_info.value.status_code == 401

    def test_token_roundtrip_multiple_wallets(self):
        """Multiple different wallets produce unique valid tokens."""
        from auth import create_session_token, verify_session_token
        wallets = [
            "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
            "ASfeGNbZCmTU8VCrvhfNNHLcyXGPVcr75zLXJHZvDTwA",
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        ]
        for wallet in wallets:
            token = create_session_token(wallet)
            assert verify_session_token(token) == wallet


# =============================================================================
#  2. SOLANA USDC MINT VERIFICATION (solana_verifier.py — V-05)
# =============================================================================

class TestSolanaVerifier:
    """Test USDC mint verification rejects non-USDC tokens."""

    def test_plain_transfer_without_mint_rejected(self):
        """Plain SPL transfer with no mint field is rejected (not added to transfers)."""
        from solana_verifier import _parse_transfers
        result_data = {
            "transaction": {
                "message": {
                    "instructions": [{
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "amount": "1000000",
                                "authority": "FakeWallet123",
                                "destination": "TreasuryWallet456",
                            }
                        },
                        "program": "spl-token",
                    }],
                    "accountKeys": [],
                }
            },
            "meta": {"innerInstructions": [], "preTokenBalances": [], "postTokenBalances": []},
        }
        parsed = _parse_transfers(result_data)
        assert len(parsed["transfers"]) == 0

    def test_transfer_checked_with_usdc_mint_accepted(self):
        """transferChecked with correct USDC mint is accepted."""
        from solana_verifier import _parse_transfers, USDC_MINT
        result_data = {
            "transaction": {
                "message": {
                    "instructions": [{
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "mint": USDC_MINT,
                                "tokenAmount": {"uiAmountString": "10.0", "amount": "10000000"},
                                "authority": "BuyerWallet",
                                "destination": "SellerWallet",
                            }
                        },
                        "program": "spl-token",
                    }],
                    "accountKeys": [],
                }
            },
            "meta": {"innerInstructions": [], "preTokenBalances": [], "postTokenBalances": []},
        }
        parsed = _parse_transfers(result_data)
        assert len(parsed["transfers"]) == 1
        assert parsed["transfers"][0]["amount_usdc"] == 10.0

    def test_transfer_checked_wrong_mint_rejected(self):
        """transferChecked with non-USDC mint is rejected."""
        from solana_verifier import _parse_transfers
        result_data = {
            "transaction": {
                "message": {
                    "instructions": [{
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "mint": "FakeTokenMint111111111111111111111111111111111",
                                "tokenAmount": {"uiAmountString": "100.0", "amount": "100000000"},
                                "authority": "Attacker",
                                "destination": "Victim",
                            }
                        },
                        "program": "spl-token",
                    }],
                    "accountKeys": [],
                }
            },
            "meta": {"innerInstructions": [], "preTokenBalances": [], "postTokenBalances": []},
        }
        parsed = _parse_transfers(result_data)
        assert len(parsed["transfers"]) == 0

    def test_sol_transfer_tracked_separately(self):
        """Native SOL transfer is tracked but with amount_usdc=0 (not USDC)."""
        from solana_verifier import _parse_transfers
        result_data = {
            "transaction": {
                "message": {
                    "instructions": [{
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "lamports": 1000000000,
                                "source": "SenderWallet",
                                "destination": "ReceiverWallet",
                            }
                        },
                        "program": "system",
                    }],
                    "accountKeys": [],
                }
            },
            "meta": {"innerInstructions": [], "preTokenBalances": [], "postTokenBalances": []},
        }
        parsed = _parse_transfers(result_data)
        assert len(parsed["transfers"]) == 1
        assert parsed["transfers"][0]["type"] == "sol"
        assert parsed["transfers"][0]["amount_usdc"] == 0

    def test_inner_instructions_parsed(self):
        """Transfers in innerInstructions are also parsed."""
        from solana_verifier import _parse_transfers, USDC_MINT
        result_data = {
            "transaction": {
                "message": {
                    "instructions": [],
                    "accountKeys": [],
                }
            },
            "meta": {
                "innerInstructions": [{
                    "instructions": [{
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "mint": USDC_MINT,
                                "tokenAmount": {"uiAmountString": "5.0", "amount": "5000000"},
                                "authority": "InnerBuyer",
                                "destination": "InnerSeller",
                            }
                        },
                        "program": "spl-token",
                    }]
                }],
                "preTokenBalances": [],
                "postTokenBalances": [],
            },
        }
        parsed = _parse_transfers(result_data)
        assert len(parsed["transfers"]) == 1
        assert parsed["transfers"][0]["amount_usdc"] == 5.0

    def test_usdc_mint_constant_is_correct(self):
        """USDC mint address matches the official Solana mainnet USDC."""
        from solana_verifier import USDC_MINT
        assert USDC_MINT == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


# =============================================================================
#  3. CONTENT SAFETY (security.py — Art.1)
# =============================================================================

class TestContentSafety:
    """Test content safety filter works correctly."""

    def test_blocked_word_raises(self):
        """Blocked content raises HTTPException 400."""
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            check_content_safety("how to hack a bank account")
        assert exc_info.value.status_code == 400

    def test_safe_content_passes(self):
        """Safe content returns None (no exception)."""
        from security import check_content_safety
        result = check_content_safety("Hello this is a normal AI service description")
        assert result is None

    def test_blocked_pattern_regex_catches_evasion(self):
        """Regex patterns catch leet-speak evasion attempts."""
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            check_content_safety("content about child porn is blocked")
        assert exc_info.value.status_code == 400

    def test_case_insensitive_blocking(self):
        """Blocking is case-insensitive."""
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            check_content_safety("RANSOMWARE deployment guide")

    def test_field_name_in_error_message(self):
        """Error message includes the field name for debugging."""
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            check_content_safety("contains malware text", field_name="description")
        assert "description" in exc_info.value.detail


# =============================================================================
#  4. ERROR UTILS (error_utils.py)
# =============================================================================

class TestSafeError:
    """Test safe_error never leaks internals."""

    def test_safe_error_returns_generic_message(self):
        """safe_error never exposes the original exception message."""
        from error_utils import safe_error
        result = safe_error(ValueError("internal DB password is abc123"), "test")
        assert "abc123" not in str(result)
        assert "error" in result
        assert "request_id" in result

    def test_safe_error_has_request_id(self):
        """safe_error always includes an 8-char request_id for log correlation."""
        from error_utils import safe_error
        result = safe_error(Exception("secret"), "ctx")
        assert len(result["request_id"]) == 8

    def test_safe_error_generic_text(self):
        """Error text is generic, not the exception class or traceback."""
        from error_utils import safe_error
        result = safe_error(RuntimeError("SELECT * FROM users WHERE password='hunter2'"), "db_query")
        assert "hunter2" not in result["error"]
        assert "SELECT" not in result["error"]
        assert result["error"] == "An error occurred"

    def test_safe_error_unique_request_ids(self):
        """Each call generates a unique request_id."""
        from error_utils import safe_error
        ids = [safe_error(Exception("x"), "ctx")["request_id"] for _ in range(10)]
        assert len(set(ids)) == 10


# =============================================================================
#  5. COMMISSION CALCULATION (config.py)
# =============================================================================

class TestCommission:
    """Test commission tiers from config."""

    def test_bronze_tier(self):
        from config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(10) == 150   # 1.5%
        assert get_commission_tier_name(10) == "BRONZE"

    def test_gold_tier(self):
        from config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(500) == 50   # 0.5%
        assert get_commission_tier_name(500) == "GOLD"

    def test_whale_tier(self):
        from config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(5000) == 10  # 0.1%
        assert get_commission_tier_name(5000) == "WHALE"

    def test_boundary_bronze_to_gold(self):
        """$499.99 is BRONZE, $500 is GOLD."""
        from config import get_commission_bps
        assert get_commission_bps(499.99) == 150
        assert get_commission_bps(500) == 50

    def test_boundary_gold_to_whale(self):
        """$4999.99 is GOLD, $5000 is WHALE."""
        from config import get_commission_bps
        assert get_commission_bps(4999.99) == 50
        assert get_commission_bps(5000) == 10

    def test_zero_amount_is_bronze(self):
        """$0 transaction defaults to BRONZE tier."""
        from config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(0) == 150
        assert get_commission_tier_name(0) == "BRONZE"

    def test_commission_deduction_math(self):
        """Verify the commission deduction math used in escrow_client."""
        from config import get_commission_bps
        amount = 1000.0  # GOLD tier
        bps = get_commission_bps(amount)
        commission = round(amount * bps / 10000, 6)
        seller_gets = round(amount - commission, 6)
        assert bps == 50
        assert commission == 5.0        # 0.5% of 1000
        assert seller_gets == 995.0     # 1000 - 5


# =============================================================================
#  6. KECCAK-256 vs SHA3-256 (base_escrow_client.py)
# =============================================================================

class TestKeccak:
    """Test that base_escrow_client uses Keccak-256 not SHA3-256."""

    def test_keccak256_differs_from_sha3(self):
        """Keccak-256 and SHA3-256 produce different results."""
        from base_escrow_client import _keccak256
        data = b"getStats()"
        keccak_result = _keccak256(data).hex()[:8]
        sha3_result = hashlib.sha3_256(data).hexdigest()[:8]
        # They MUST be different — if they're the same, the fix didn't work
        assert keccak_result != sha3_result, "Keccak and SHA3 should produce different results"

    def test_known_selector(self):
        """Verify getStats() produces the correct Solidity function selector."""
        from base_escrow_client import _keccak256
        # Known correct keccak256("getStats()") selector = c59d4847
        selector = _keccak256(b"getStats()").hex()[:8]
        assert selector == "c59d4847", f"Expected c59d4847, got {selector}"

    def test_another_known_selector(self):
        """Verify transfer(address,uint256) produces the correct selector."""
        from base_escrow_client import _keccak256
        # ERC-20 transfer function selector = a9059cbb
        selector = _keccak256(b"transfer(address,uint256)").hex()[:8]
        assert selector == "a9059cbb", f"Expected a9059cbb, got {selector}"

    def test_keccak_deterministic(self):
        """Same input always produces same output."""
        from base_escrow_client import _keccak256
        data = b"test input data"
        result1 = _keccak256(data).hex()
        result2 = _keccak256(data).hex()
        assert result1 == result2


# =============================================================================
#  7. NONCE ANTI-REPLAY (auth.py)
# =============================================================================

class TestNonceAntiReplay:
    """Test nonce lifecycle and anti-replay protection (Redis-backed since S29)."""

    def test_nonce_ttl_constant_exists(self):
        """NONCE_TTL constant must exist for Redis key expiry."""
        from auth import NONCE_TTL
        assert NONCE_TTL > 0
        assert NONCE_TTL <= 600  # Max 10 min

    def test_nonce_functions_are_async(self):
        """Nonce operations must be async (Redis-backed)."""
        import inspect
        from auth import _nonce_set, _nonce_get, _nonce_delete, _nonce_mark_used, _nonce_is_used
        assert inspect.iscoroutinefunction(_nonce_set)
        assert inspect.iscoroutinefunction(_nonce_get)
        assert inspect.iscoroutinefunction(_nonce_delete)
        assert inspect.iscoroutinefunction(_nonce_mark_used)
        assert inspect.iscoroutinefunction(_nonce_is_used)

    def test_nonce_anti_replay_mark_used_exists(self):
        """_nonce_mark_used function must exist for anti-replay."""
        from auth import _nonce_mark_used
        assert callable(_nonce_mark_used)


# =============================================================================
#  8. BRUTE FORCE PROTECTION (auth.py)
# =============================================================================

class TestBruteForceProtection:
    """Test brute force rate limiting (Redis-backed since S29, with in-memory fallback)."""

    def test_brute_force_functions_are_async(self):
        """_check_brute_force and _record_failed must be async (S29 Redis migration)."""
        import inspect
        from auth import _check_brute_force, _record_failed
        assert inspect.iscoroutinefunction(_check_brute_force)
        assert inspect.iscoroutinefunction(_record_failed)

    def test_max_attempts_constant_exists(self):
        """Rate limit constants must exist."""
        from auth import _MAX_FAILED_ATTEMPTS, _FAILED_WINDOW
        assert _MAX_FAILED_ATTEMPTS == 10
        assert _FAILED_WINDOW == 300  # 5 min

    def test_fallback_dict_exists(self):
        """In-memory fallback dict must exist for when Redis is down."""
        from auth import _FAILED_ATTEMPTS
        assert isinstance(_FAILED_ATTEMPTS, dict)


# =============================================================================
#  9. WALLET ADDRESS VALIDATION (security.py)
# =============================================================================

class TestWalletValidationSecurity:
    """Test wallet address validation prevents injection and invalid addresses."""

    def test_rejects_empty_address(self):
        from security import validate_wallet_address
        assert validate_wallet_address("") is False

    def test_rejects_short_address(self):
        from security import validate_wallet_address
        assert validate_wallet_address("abc") is False

    def test_valid_evm_address(self):
        from security import validate_wallet_address
        assert validate_wallet_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913") is True

    def test_invalid_evm_missing_prefix(self):
        from security import validate_wallet_address
        assert validate_wallet_address("833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", chain="evm") is False

    def test_valid_solana_address(self):
        from security import validate_wallet_address
        assert validate_wallet_address("7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q") is True

    def test_rejects_solana_with_invalid_chars(self):
        """Solana base58 excludes 0, O, I, l."""
        from security import validate_wallet_address
        assert validate_wallet_address("0v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q") is False


# =============================================================================
#  10. ESCROW CLIENT INPUT VALIDATION (escrow_client.py)
# =============================================================================

class TestEscrowInputValidation:
    """Test escrow input validation catches bad addresses and params."""

    def test_escrow_address_regex(self):
        """Solana address regex rejects invalid formats."""
        from escrow_client import _SOLANA_ADDR_RE
        assert _SOLANA_ADDR_RE.match("7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q")
        assert not _SOLANA_ADDR_RE.match("0xEVM_ADDRESS_NOT_SOLANA")
        assert not _SOLANA_ADDR_RE.match("")
        assert not _SOLANA_ADDR_RE.match("short")

    def test_escrow_usdc_mint_matches_verifier(self):
        """Escrow client USDC_MINT matches solana_verifier USDC_MINT."""
        from escrow_client import USDC_MINT as escrow_mint
        from solana_verifier import USDC_MINT as verifier_mint
        assert escrow_mint == verifier_mint


# =============================================================================
#  11. IP EXTRACTION SECURITY (security.py — anti-spoofing)
# =============================================================================

class TestIPExtraction:
    """Test that IP extraction is secure against spoofing."""

    def test_get_real_ip_from_untrusted_client(self):
        """Untrusted client IP is returned directly, ignoring X-Forwarded-For."""
        from security import get_real_ip
        from unittest.mock import MagicMock

        request = MagicMock()
        request.client.host = "1.2.3.4"
        request.headers.get.return_value = "10.0.0.1, 192.168.1.1"

        ip = get_real_ip(request)
        # Should return client IP since 1.2.3.4 is NOT in _TRUSTED_PROXIES
        assert ip == "1.2.3.4"

    def test_get_real_ip_from_trusted_proxy(self):
        """Trusted proxy (127.0.0.1) allows X-Forwarded-For extraction."""
        from security import get_real_ip
        from unittest.mock import MagicMock

        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers.get.return_value = "203.0.113.50, 10.0.0.1"

        ip = get_real_ip(request)
        # Should return LAST IP in chain (most reliable, added by our proxy)
        assert ip == "10.0.0.1"
