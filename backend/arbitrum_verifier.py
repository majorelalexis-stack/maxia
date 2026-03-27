"""MAXIA Art.13 — Arbitrum One Verifier & x402 EVM Support
Production-hardened: RPC fallback, rate limiting, proper logging, min amount checks.
"""
import os, asyncio, time, logging
import httpx
from config import (
    ARBITRUM_RPC, ARBITRUM_CHAIN_ID, ARBITRUM_USDC_CONTRACT,
    X402_FACILITATOR_URL, TREASURY_ADDRESS_ARBITRUM,
)
from error_utils import safe_error

logger = logging.getLogger("maxia.arbitrum_verifier")

# ── RPC fallback list ──
ARBITRUM_RPC_URLS = [
    os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc"),
    "https://rpc.ankr.com/arbitrum",
    "https://arbitrum.llamarpc.com",
]

# ── USDC contract assertion at module load ──
_EXPECTED_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
if ARBITRUM_USDC_CONTRACT.lower() != _EXPECTED_USDC.lower():
    logger.critical(
        f"[ArbitrumVerifier] ARBITRUM_USDC_CONTRACT mismatch! "
        f"Got {ARBITRUM_USDC_CONTRACT}, expected {_EXPECTED_USDC}. "
        f"Payments will fail!"
    )

# ── Facilitator URL HTTPS check ──
if X402_FACILITATOR_URL and not X402_FACILITATOR_URL.startswith("https://"):
    logger.warning(
        f"[ArbitrumVerifier] X402_FACILITATOR_URL is not HTTPS: {X402_FACILITATOR_URL}. "
        f"This is insecure in production!"
    )

# ── RPC rate limiter — max 100 calls/min ──
_RPC_CALL_LIMIT = 100
_rpc_calls: list[float] = []
_rpc_lock = asyncio.Lock()

ARBITRUM_MIN_TX_USDC = float(os.getenv("ARBITRUM_MIN_TX_USDC", "0.01"))


async def _check_rpc_rate_limit():
    """Enforce max RPC calls per minute. Raises if exceeded."""
    async with _rpc_lock:
        now = time.monotonic()
        while _rpc_calls and _rpc_calls[0] < now - 60:
            _rpc_calls.pop(0)
        if len(_rpc_calls) >= _RPC_CALL_LIMIT:
            raise RuntimeError(f"RPC rate limit exceeded ({_RPC_CALL_LIMIT} calls/min)")
        _rpc_calls.append(now)


async def _rpc_post(payload: dict, timeout: float = 20) -> dict:
    """Post to Arbitrum RPC with fallback across multiple endpoints."""
    await _check_rpc_rate_limit()
    last_error = None
    for rpc_url in ARBITRUM_RPC_URLS:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(rpc_url, json=payload)
                data = resp.json()
            if "error" in data and data["error"]:
                logger.warning(f"[ArbitrumVerifier] RPC {rpc_url} returned error: {data['error']}")
                last_error = Exception(f"RPC error: {data['error']}")
                continue
            return data
        except httpx.TimeoutException as e:
            logger.warning(f"[ArbitrumVerifier] RPC {rpc_url} timeout: {e}")
            last_error = e
        except httpx.ConnectError as e:
            logger.warning(f"[ArbitrumVerifier] RPC {rpc_url} connect error: {e}")
            last_error = e
        except Exception as e:
            logger.warning(f"[ArbitrumVerifier] RPC {rpc_url} unexpected error: {type(e).__name__}: {e}")
            last_error = e
    raise last_error or Exception("All Arbitrum RPC endpoints failed")


async def verify_arbitrum_transaction(tx_hash: str, expected_to: str = None) -> dict:
    """Verify a transaction on Arbitrum One via eth_getTransactionReceipt."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    for attempt in range(3):
        try:
            data = await _rpc_post(payload)
            result = data.get("result")
            if not result:
                await asyncio.sleep(2 ** attempt)
                continue
            if result.get("status") != "0x1":
                return {"valid": False, "error": "Transaction reverted"}
            if expected_to and result.get("to", "").lower() != expected_to.lower():
                return {"valid": False, "error": "Recipient mismatch"}
            logger.info(f"[ArbitrumVerifier] TX verified: {tx_hash[:16]}... block={result.get('blockNumber')}")
            return {
                "valid": True,
                "blockNumber": int(result.get("blockNumber", "0x0"), 16),
                "from": result.get("from", ""),
                "to": result.get("to", ""),
                "gasUsed": int(result.get("gasUsed", "0x0"), 16),
                "network": "arbitrum-mainnet",
                "chainId": ARBITRUM_CHAIN_ID,
            }
        except RuntimeError as e:
            result = safe_error(e, "arbitrum_verify_tx")
            result["valid"] = False
            return result
        except httpx.TimeoutException as e:
            logger.warning(f"[ArbitrumVerifier] verify_arbitrum_transaction attempt {attempt + 1} timeout")
            await asyncio.sleep(2 ** attempt)
        except httpx.ConnectError as e:
            logger.warning(f"[ArbitrumVerifier] verify_arbitrum_transaction attempt {attempt + 1} connect error")
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"[ArbitrumVerifier] verify_arbitrum_transaction attempt {attempt + 1} failed: {type(e).__name__}")
            await asyncio.sleep(2 ** attempt)
    return {"valid": False, "error": "Verification failed after retries"}


async def verify_usdc_transfer_arbitrum(tx_hash: str, expected_amount_raw: int = None,
                                         expected_recipient: str = None) -> dict:
    """Verify a USDC ERC-20 Transfer event on Arbitrum with recipient + amount check."""
    if not expected_recipient:
        if not TREASURY_ADDRESS_ARBITRUM:
            return {"valid": False, "error": "TREASURY_ADDRESS_ARBITRUM not configured"}
        expected_recipient = TREASURY_ADDRESS_ARBITRUM

    if expected_amount_raw is not None and expected_amount_raw > 0:
        min_raw = int(ARBITRUM_MIN_TX_USDC * 1e6)
        if expected_amount_raw < min_raw:
            return {
                "valid": False,
                "error": f"Amount below minimum: ${expected_amount_raw / 1e6:.4f} < ${ARBITRUM_MIN_TX_USDC}",
            }

    receipt = await verify_arbitrum_transaction(tx_hash, expected_to=None)
    if not receipt.get("valid"):
        return receipt

    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    try:
        data = await _rpc_post(payload)
        logs = data.get("result", {}).get("logs", [])
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            if (log.get("address", "").lower() != ARBITRUM_USDC_CONTRACT.lower()
                    or topics[0] != transfer_topic):
                continue
            if len(topics[1]) < 42 or len(topics[2]) < 42:
                logger.warning(f"[ArbitrumVerifier] Malformed topics in tx {tx_hash}: len(topics[1])={len(topics[1])}, len(topics[2])={len(topics[2])}")
                continue

            amount = int(log.get("data", "0x0"), 16)
            from_addr = "0x" + topics[1][-40:]
            to_addr = "0x" + topics[2][-40:]

            if expected_recipient and to_addr.lower() != expected_recipient.lower():
                return {
                    "valid": False,
                    "error": f"Recipient mismatch: {to_addr} != {expected_recipient}",
                }
            if expected_amount_raw and amount < expected_amount_raw:
                return {
                    "valid": False,
                    "error": f"Insufficient: {amount / 1e6:.2f} USDC < {expected_amount_raw / 1e6:.2f} USDC",
                }

            receipt["usdcTransfer"] = {
                "from": from_addr,
                "to": to_addr,
                "amount_raw": amount,
                "amount_usdc": amount / 1e6,
            }
            logger.info(
                f"[ArbitrumVerifier] USDC transfer verified: {tx_hash[:16]}... "
                f"{from_addr[:10]}...->{to_addr[:10]}... {amount / 1e6:.2f} USDC"
            )
            return receipt
        return {"valid": False, "error": "No USDC transfer found in logs"}
    except RuntimeError as e:
        result = safe_error(e, "arbitrum_verify_usdc")
        result["valid"] = False
        return result
    except httpx.TimeoutException as e:
        result = safe_error(e, "arbitrum_verify_usdc_timeout")
        result["valid"] = False
        return result
    except httpx.ConnectError as e:
        result = safe_error(e, "arbitrum_verify_usdc_connect")
        result["valid"] = False
        return result
    except Exception as e:
        result = safe_error(e, "arbitrum_verify_usdc")
        result["valid"] = False
        return result


async def x402_verify_payment_arbitrum(payment_header: str, expected_amount_usdc: float) -> dict:
    """Verify an x402 payment on Arbitrum via the facilitator, with direct on-chain fallback."""
    if X402_FACILITATOR_URL and not X402_FACILITATOR_URL.startswith("https://"):
        logger.warning(f"[x402] Facilitator URL is not HTTPS: {X402_FACILITATOR_URL}")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{X402_FACILITATOR_URL}/verify",
                json={
                    "paymentPayload": payment_header,
                    "network": "arbitrum-mainnet",
                    "expectedAmount": str(int(expected_amount_usdc * 1e6)),
                },
            )
            result = resp.json()
        if resp.status_code == 200 and result.get("valid"):
            logger.info(f"[x402] Arbitrum payment verified via facilitator: {result.get('txHash', '')[:16]}...")
            return {
                "valid": True,
                "txHash": result.get("txHash", ""),
                "network": "arbitrum-mainnet",
                "settledAmount": result.get("settledAmount"),
            }
        logger.warning(f"[x402] Facilitator rejected: {result.get('error', 'unknown')}")
    except httpx.TimeoutException as e:
        logger.warning(f"[x402] Facilitator timeout: {e}")
    except httpx.ConnectError as e:
        logger.warning(f"[x402] Facilitator connect error: {e}")
    except Exception as e:
        logger.warning(f"[x402] Facilitator error: {type(e).__name__}: {e}")

    if payment_header and payment_header.startswith("0x") and len(payment_header) == 66:
        logger.info(f"[x402] Attempting direct on-chain fallback for {payment_header[:16]}...")
        try:
            direct_result = await verify_usdc_transfer_arbitrum(
                tx_hash=payment_header,
                expected_amount_raw=int(expected_amount_usdc * 1e6),
            )
            if direct_result.get("valid"):
                logger.info(f"[x402] Direct on-chain verification succeeded for {payment_header[:16]}...")
                direct_result["verifiedVia"] = "direct-onchain-fallback"
                direct_result["txHash"] = payment_header
                return direct_result
            logger.warning(f"[x402] Direct on-chain fallback failed: {direct_result.get('error')}")
        except Exception as e:
            logger.error(f"[x402] Direct on-chain fallback error: {type(e).__name__}: {e}")

    return {"valid": False, "error": "Facilitator rejected and direct verification failed"}


def build_x402_challenge_arbitrum(path: str, price_usdc: float, pay_to: str) -> dict:
    """Build an x402 402-response payload for Arbitrum network."""
    return {
        "scheme": "exact",
        "network": "arbitrum-mainnet",
        "maxAmountRequired": str(int(price_usdc * 1e6)),
        "resource": path,
        "description": f"MAXIA service: {path}",
        "mimeType": "application/json",
        "payTo": pay_to,
        "asset": ARBITRUM_USDC_CONTRACT,
        "maxTimeoutSeconds": 60,
        "extra": {"chainId": ARBITRUM_CHAIN_ID, "facilitator": X402_FACILITATOR_URL},
    }
