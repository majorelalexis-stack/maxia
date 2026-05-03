"""x402 payment header parser — pure functions, no side effects."""
import json
from typing import Optional


def parse_x402_header(header_value: str) -> Optional[str]:
    """
    Parse X-Payment header, return Solana tx signature or None.

    Accepts two formats:
    - Raw base58 signature string
    - JSON: {"x402Version":1,"network":"solana","payload":{"signature":"..."}}
    """
    if not header_value:
        return None
    header_value = header_value.strip()
    if header_value.startswith("{"):
        try:
            data = json.loads(header_value)
            sig = data.get("payload", {}).get("signature", "")
            network = data.get("network", "solana")
            if network != "solana":
                return None
            if sig and len(sig) >= 32:
                return sig
        except (json.JSONDecodeError, AttributeError, TypeError):
            return None
        return None
    # Raw base58 Solana signature (87-88 chars typically)
    if len(header_value) >= 32:
        return header_value
    return None


def build_x402_challenge(price_usdc: float, treasury: str) -> dict:
    """Build 402 response body per x402 spec."""
    return {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": "exact",
                "network": "solana",
                "maxAmountRequired": str(int(price_usdc * 1_000_000)),
                "asset": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "payTo": treasury,
                "memo": "MAXIA marketplace payment",
            }
        ],
        "error": "Payment Required",
    }
