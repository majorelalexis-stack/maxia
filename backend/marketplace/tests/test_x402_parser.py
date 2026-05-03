"""TDD — x402 parser unit tests. Run: pytest marketplace/tests/test_x402_parser.py"""
import pytest
from marketplace.x402_parser import parse_x402_header, build_x402_challenge

VALID_SIG = "5J8zXvKqLm2NpRtYwBcEfGhJdKsAuViPxMnOqRsTuVwXyZ3a4b5c6d7e8f9g0h1i2j"


class TestParseX402Header:

    def test_none_on_empty(self):
        assert parse_x402_header("") is None
        assert parse_x402_header("   ") is None

    def test_raw_signature_returned(self):
        assert parse_x402_header(VALID_SIG) == VALID_SIG

    def test_raw_too_short_rejected(self):
        assert parse_x402_header("tooshort") is None

    def test_json_format_extracts_signature(self):
        import json
        header = json.dumps({
            "x402Version": 1,
            "network": "solana",
            "payload": {"signature": VALID_SIG},
        })
        assert parse_x402_header(header) == VALID_SIG

    def test_json_wrong_network_rejected(self):
        import json
        header = json.dumps({
            "x402Version": 1,
            "network": "base",
            "payload": {"signature": VALID_SIG},
        })
        assert parse_x402_header(header) is None

    def test_json_missing_signature_returns_none(self):
        import json
        header = json.dumps({"x402Version": 1, "network": "solana", "payload": {}})
        assert parse_x402_header(header) is None

    def test_invalid_json_returns_none(self):
        assert parse_x402_header("{not valid json}") is None

    def test_whitespace_trimmed(self):
        assert parse_x402_header(f"  {VALID_SIG}  ") == VALID_SIG


class TestBuildX402Challenge:

    def test_structure(self):
        result = build_x402_challenge(2.99, "7RtCpikgfd6xiFQyVoxjV51HN14XXRrQJiJ3KrzUdQsW")
        assert result["x402Version"] == 1
        assert len(result["accepts"]) == 1
        accept = result["accepts"][0]
        assert accept["scheme"] == "exact"
        assert accept["network"] == "solana"
        assert accept["payTo"] == "7RtCpikgfd6xiFQyVoxjV51HN14XXRrQJiJ3KrzUdQsW"

    def test_amount_in_micro_usdc(self):
        result = build_x402_challenge(1.0, "treasury")
        assert result["accepts"][0]["maxAmountRequired"] == "1000000"

    def test_error_field_present(self):
        result = build_x402_challenge(0.01, "treasury")
        assert result["error"] == "Payment Required"
