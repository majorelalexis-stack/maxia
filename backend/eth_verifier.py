"""MAXIA Art.13 Extended — Ethereum Mainnet Verifier (grosses transactions uniquement)
Production-hardened: RPC fallback, rate limiting, proper logging, min amount checks.
"""
import os, logging, asyncio, time
import httpx
from config import ETH_RPC, ETH_USDC_CONTRACT, ETH_CHAIN_ID, ETH_MIN_TX_USDC
from http_client import get_http_client
from error_utils import safe_error

logger = logging.getLogger("maxia.eth_verifier")

# ── #1: RPC fallback list ──
ETH_RPC_URLS = [
    ETH_RPC,
    "https://eth.llamarpc.com",
    "https://ethereum.publicnode.com",
    "https://rpc.ankr.com/eth",
]

# ── #7: USDC contract assertion at module load ──
_EXPECTED_ETH_USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
if ETH_USDC_CONTRACT.lower() != _EXPECTED_ETH_USDC.lower():
    logger.critical(
        f"[EthVerifier] ETH_USDC_CONTRACT mismatch! "
        f"Got {ETH_USDC_CONTRACT}, expected {_EXPECTED_ETH_USDC}. "
        f"Payments will fail!"
    )

# ── #2: RPC rate limiter — max 100 calls/min ──
_RPC_CALL_LIMIT = 100
_rpc_calls: list[float] = []
_rpc_lock = asyncio.Lock()


async def _check_rpc_rate_limit():
    """Enforce max RPC calls per minute. Raises if exceeded."""
    async with _rpc_lock:
        now = time.monotonic()
        # Evict calls older than 60s
        while _rpc_calls and _rpc_calls[0] < now - 60:
            _rpc_calls.pop(0)
        if len(_rpc_calls) >= _RPC_CALL_LIMIT:
            raise RuntimeError(f"ETH RPC rate limit exceeded ({_RPC_CALL_LIMIT} calls/min)")
        _rpc_calls.append(now)


async def _rpc_post(payload: dict, timeout: float = 20) -> dict:
    """Post to ETH RPC with fallback across multiple endpoints.
    Tries each RPC URL in order; returns first successful response.
    #1: RPC fallback, #2: rate limit, #5: specific exception handling, #8: RPC error field check.
    """
    await _check_rpc_rate_limit()
    last_error = None
    for rpc_url in ETH_RPC_URLS:
        try:
            client = get_http_client()
            resp = await client.post(rpc_url, json=payload, timeout=timeout)
            if resp.status_code != 200 or not resp.text.strip().startswith("{"):
                last_error = Exception(f"RPC {rpc_url}: HTTP {resp.status_code}")
                continue
            data = resp.json()
            if "error" in data and data["error"]:
                logger.warning(f"[EthVerifier] RPC {rpc_url} returned error: {data['error']}")
                last_error = Exception(f"RPC error: {data['error']}")
                continue
            return data
        except httpx.TimeoutException as e:
            logger.warning(f"[EthVerifier] RPC {rpc_url} timeout: {e}")
            last_error = e
        except httpx.ConnectError as e:
            logger.warning(f"[EthVerifier] RPC {rpc_url} connect error: {e}")
            last_error = e
        except Exception as e:
            logger.debug(f"[EthVerifier] RPC {rpc_url} unexpected error: {type(e).__name__}: {e}")
            last_error = e
    raise last_error or Exception("All ETH RPC endpoints failed")


async def verify_eth_transaction(tx_hash: str, expected_to: str = None) -> dict:
    """Verify a transaction on Ethereum mainnet via eth_getTransactionReceipt.
    #1: RPC fallback, #4: logging, #5: specific exception handling.
    """
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
            # #9: Log successful verification
            logger.info(f"[EthVerifier] TX verified: {tx_hash[:16]}... block={result.get('blockNumber')}")
            return {
                "valid": True,
                "blockNumber": int(result.get("blockNumber", "0x0"), 16),
                "from": result.get("from", ""),
                "to": result.get("to", ""),
                "gasUsed": int(result.get("gasUsed", "0x0"), 16),
                "network": "ethereum-mainnet",
                "chainId": ETH_CHAIN_ID,
            }
        except RuntimeError as e:
            # Rate limit exceeded — don't retry
            result = safe_error(e, "eth_verify_tx")
            result["valid"] = False
            return result
        except httpx.TimeoutException as e:
            logger.warning(f"[EthVerifier] verify_eth_transaction attempt {attempt + 1} timeout")
            await asyncio.sleep(2 ** attempt)
        except httpx.ConnectError as e:
            logger.warning(f"[EthVerifier] verify_eth_transaction attempt {attempt + 1} connect error")
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"[EthVerifier] verify_eth_transaction attempt {attempt + 1} failed: {type(e).__name__}")
            await asyncio.sleep(2 ** attempt)
    return {"valid": False, "error": "Verification failed after retries"}


async def verify_usdc_transfer_eth(tx_hash: str, expected_amount_raw: int = None,
                                    expected_recipient: str = None) -> dict:
    """Verify a USDC ERC-20 Transfer event on Ethereum mainnet with recipient + amount check.
    #3: address extraction validation, #6: treasury validation,
    #7: USDC contract check, #9: success logging.
    """
    # #6: Treasury validation — handle missing treasury config gracefully
    if not expected_recipient:
        from config import TREASURY_ADDRESS_ETH
        expected_recipient = TREASURY_ADDRESS_ETH
    if not expected_recipient:
        return {"valid": False, "error": "ETH treasury not configured (TREASURY_ADDRESS_ETH missing)"}

    # Seuil minimum pour Ethereum (gas fees elevees)
    if expected_amount_raw and expected_amount_raw < int(ETH_MIN_TX_USDC * 1e6):
        return {
            "valid": False,
            "error": f"Montant trop faible pour Ethereum mainnet (min {ETH_MIN_TX_USDC} USDC). Utilisez Solana ou Base pour les petites transactions.",
        }

    receipt = await verify_eth_transaction(tx_hash, expected_to=None)
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

            # #3: Address extraction validation — ensure topics has enough entries and correct length
            if len(topics) < 3:
                continue
            if (log.get("address", "").lower() != ETH_USDC_CONTRACT.lower()
                    or topics[0] != transfer_topic):
                continue
            if len(topics[1]) < 42 or len(topics[2]) < 42:
                logger.warning(f"[EthVerifier] Malformed topics in tx {tx_hash}: len(topics[1])={len(topics[1])}, len(topics[2])={len(topics[2])}")
                continue

            amount = int(log.get("data", "0x0"), 16)
            from_addr = "0x" + topics[1][-40:]
            to_addr = "0x" + topics[2][-40:]

            # Verifier le destinataire
            if expected_recipient and to_addr.lower() != expected_recipient.lower():
                return {
                    "valid": False,
                    "error": f"Recipient mismatch: {to_addr} != {expected_recipient}",
                }

            # Verifier le montant
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
            # #9: Log successful USDC verification
            logger.info(
                f"[EthVerifier] USDC transfer verified: {tx_hash[:16]}... "
                f"{from_addr[:10]}...->{to_addr[:10]}... {amount / 1e6:.2f} USDC"
            )
            return receipt
        return {"valid": False, "error": "No USDC transfer found in logs"}
    except RuntimeError as e:
        result = safe_error(e, "eth_verify_usdc")
        result["valid"] = False
        return result
    except httpx.TimeoutException as e:
        result = safe_error(e, "eth_verify_usdc_timeout")
        result["valid"] = False
        return result
    except httpx.ConnectError as e:
        result = safe_error(e, "eth_verify_usdc_connect")
        result["valid"] = False
        return result
    except Exception as e:
        result = safe_error(e, "eth_verify_usdc")
        result["valid"] = False
        return result


async def x402_verify_payment_eth(payment_header: str, expected_amount_usdc: float) -> dict:
    """Verify an x402 payment on Ethereum mainnet via direct on-chain verification."""
    # #14: HTTPS facilitator check (runtime warning)
    from config import X402_FACILITATOR_URL
    if X402_FACILITATOR_URL and not X402_FACILITATOR_URL.startswith("https://"):
        logger.warning(f"[x402-eth] Facilitator URL is not HTTPS: {X402_FACILITATOR_URL}")

    result = await verify_usdc_transfer_eth(
        tx_hash=payment_header,
        expected_amount_raw=int(expected_amount_usdc * 1e6),
    )
    if result.get("valid"):
        logger.info(f"[x402-eth] Payment verified: {payment_header[:16]}... {expected_amount_usdc} USDC")
    return result


async def verify_eth_value_transfer(tx_hash: str, expected_recipient: str = None,
                                     min_eth: float = None) -> dict:
    """Verify a native ETH value transfer (not ERC-20).
    #10: Min amount enforcement via ETH_MIN_TX_USDC equivalent check.
    """
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionByHash",
        "params": [tx_hash],
    }
    try:
        data = await _rpc_post(payload)
        tx = data.get("result")
        if not tx:
            return {"valid": False, "error": "Transaction not found"}

        value_wei = int(tx.get("value", "0x0"), 16)
        value_eth = value_wei / 1e18
        to_addr = tx.get("to", "")

        if expected_recipient and to_addr.lower() != expected_recipient.lower():
            return {"valid": False, "error": f"Recipient mismatch: {to_addr}"}

        if min_eth and value_eth < min_eth:
            return {"valid": False, "error": f"Insufficient: {value_eth:.6f} ETH < {min_eth:.6f} ETH"}

        # #10: Minimum value enforcement — reject dust-level ETH transfers
        # ETH_MIN_TX_USDC is the $ threshold; assuming ~$2000/ETH as conservative floor
        min_eth_value = ETH_MIN_TX_USDC / 4000  # very conservative: $10 / $4000 = 0.0025 ETH
        if value_eth < min_eth_value:
            return {
                "valid": False,
                "error": f"ETH value too low: {value_eth:.6f} ETH < {min_eth_value:.6f} ETH minimum (Ethereum mainnet only for larger transactions)",
            }

        receipt = await verify_eth_transaction(tx_hash)
        if not receipt.get("valid"):
            return receipt

        receipt["ethTransfer"] = {
            "from": tx.get("from", ""),
            "to": to_addr,
            "value_wei": value_wei,
            "value_eth": value_eth,
        }
        # #9: Log successful verification
        logger.info(f"[EthVerifier] ETH transfer verified: {tx_hash[:16]}... {value_eth:.6f} ETH")
        return receipt
    except RuntimeError as e:
        result = safe_error(e, "eth_verify_value_transfer")
        result["valid"] = False
        return result
    except httpx.TimeoutException as e:
        result = safe_error(e, "eth_verify_value_transfer_timeout")
        result["valid"] = False
        return result
    except httpx.ConnectError as e:
        result = safe_error(e, "eth_verify_value_transfer_connect")
        result["valid"] = False
        return result
    except Exception as e:
        result = safe_error(e, "eth_verify_value_transfer")
        result["valid"] = False
        return result


# ══════════════════════════════════════════
# Fonctions de scan pour SCOUT agent
# ══════════════════════════════════════════

async def get_eth_block_number() -> int:
    """Get current Ethereum block number."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_blockNumber",
        "params": [],
    }
    try:
        data = await _rpc_post(payload, timeout=10)
        return int(data.get("result", "0x0"), 16)
    except Exception as e:
        logger.warning(f"[EthVerifier] get_eth_block_number failed: {e}")
        return 0


async def get_contract_logs(contract_address: str, from_block: str = None,
                             topic0: str = None) -> list:
    """Get event logs for a contract (used by SCOUT to find AI agent interactions)."""
    if not from_block:
        current = await get_eth_block_number()
        from_block = hex(max(0, current - 5000))  # ~last 18h

    params = {
        "address": contract_address,
        "fromBlock": from_block,
        "toBlock": "latest",
    }
    if topic0:
        params["topics"] = [topic0]
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getLogs",
        "params": [params],
    }
    try:
        data = await _rpc_post(payload, timeout=30)
        return data.get("result", [])
    except Exception as e:
        logger.error(f"[EthVerifier] get_contract_logs error: {type(e).__name__}: {e}")
        return []


async def get_wallet_tx_count(address: str) -> int:
    """Get transaction count for a wallet (nonce = activity level)."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionCount",
        "params": [address, "latest"],
    }
    try:
        data = await _rpc_post(payload, timeout=10)
        return int(data.get("result", "0x0"), 16)
    except Exception as e:
        logger.warning(f"[EthVerifier] get_wallet_tx_count failed: {e}")
        return 0
