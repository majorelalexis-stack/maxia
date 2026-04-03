"""MAXIA Unified EVM Verifier -- base class for all EVM chain transaction verification.

Replaces 7 copy-pasted verifier files (base, eth, polygon, arbitrum, avalanche, bnb, sei)
with one reusable class.  Each chain instantiates EvmVerifier with its own config.

Production-hardened: async RPC rate limiting, multi-endpoint fallback, exponential backoff
retries, USDC ERC-20 Transfer event parsing, x402 facilitator + on-chain fallback,
min-amount enforcement, safe_error() integration, proper logging.

Usage (example -- Base L2):
    from blockchain.evm_verifier_base import EvmVerifier
    base_verifier = EvmVerifier(
        chain_name="Base",
        chain_id=8453,
        network_id="base-mainnet",
        rpc_urls=["https://mainnet.base.org", "https://base.llamarpc.com"],
        usdc_contract="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        treasury_address=os.getenv("TREASURY_ADDRESS_BASE", ""),
        min_tx_usdc=0.01,
    )
    result = await base_verifier.verify_transaction(tx_hash)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from core.error_utils import safe_error
from core.http_client import get_http_client

logger = logging.getLogger("maxia.evm_verifier")

# ERC-20 Transfer(address indexed from, address indexed to, uint256 value)
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class EvmVerifier:
    """Generic EVM chain transaction verifier with RPC failover and rate limiting.

    Thread-safe: each instance has its own asyncio.Lock for rate limiting.
    Immutable config: chain parameters are set at __init__ and never mutated.
    """

    _RPC_CALL_LIMIT: int = 100
    _RPC_CALL_WINDOW: int = 60  # seconds
    _MAX_RETRIES: int = 3
    _DEFAULT_TIMEOUT: float = 20.0

    def __init__(
        self,
        chain_name: str,
        chain_id: int,
        network_id: str,
        rpc_urls: list[str],
        usdc_contract: str,
        treasury_address: str,
        min_tx_usdc: float = 0.01,
        expected_usdc_contract: Optional[str] = None,
        usdt_contract: str = "",
    ) -> None:
        """Initialize the verifier for a specific EVM chain.

        Args:
            chain_name: Human-readable chain name (e.g. "Base", "Ethereum", "Polygon").
            chain_id: EVM chain ID (e.g. 8453 for Base, 1 for Ethereum).
            network_id: Network identifier for x402 (e.g. "base-mainnet").
            rpc_urls: Ordered list of RPC endpoints to try (first = preferred).
            usdc_contract: USDC ERC-20 contract address on this chain.
            treasury_address: Default recipient for USDC transfers (from env).
            min_tx_usdc: Minimum USDC amount for transactions on this chain.
            expected_usdc_contract: If provided, assert USDC contract matches at init.
            usdt_contract: Optional USDT ERC-20 contract address on this chain.
        """
        if not rpc_urls:
            raise ValueError(f"{chain_name}: rpc_urls cannot be empty")

        self.chain_name = chain_name
        self.chain_id = chain_id
        self.network_id = network_id
        self.rpc_urls = list(rpc_urls)  # defensive copy
        self.usdc_contract = usdc_contract.lower()
        self.usdt_contract = usdt_contract.lower() if usdt_contract else ""
        # Set of all accepted stablecoin contracts for this chain
        self.accepted_stablecoins = {self.usdc_contract}
        if self.usdt_contract:
            self.accepted_stablecoins.add(self.usdt_contract)
        self.treasury_address = treasury_address.lower() if treasury_address else ""
        self.min_tx_usdc = min_tx_usdc

        # Per-instance rate limiter state
        self._rpc_calls: list[float] = []
        self._rpc_lock = asyncio.Lock()

        # USDC contract assertion at init (same as module-level checks in existing files)
        if expected_usdc_contract:
            if self.usdc_contract != expected_usdc_contract.lower():
                logger.critical(
                    f"[{chain_name}Verifier] USDC contract mismatch! "
                    f"Got {usdc_contract}, expected {expected_usdc_contract}. "
                    f"Payments will fail!"
                )

        # Basic address format validation
        if not self.usdc_contract.startswith("0x") or len(self.usdc_contract) != 42:
            logger.critical(
                f"[{chain_name}Verifier] Invalid USDC contract format: {usdc_contract}"
            )

        logger.info(
            f"[{chain_name}Verifier] Initialized -- "
            f"chain_id={chain_id}, network={network_id}, "
            f"USDC={usdc_contract[:10]}..., "
            f"treasury={'(not set)' if not treasury_address else treasury_address[:10] + '...'}, "
            f"min_tx=${min_tx_usdc}, rpcs={len(rpc_urls)}"
        )

    # ------------------------------------------------------------------ #
    #  RPC layer -- rate limiting + multi-endpoint fallback               #
    # ------------------------------------------------------------------ #

    async def _check_rpc_rate_limit(self) -> None:
        """Enforce max RPC calls per minute. Raises RuntimeError if exceeded."""
        async with self._rpc_lock:
            now = time.monotonic()
            # Evict calls older than the window
            while self._rpc_calls and self._rpc_calls[0] < now - self._RPC_CALL_WINDOW:
                self._rpc_calls.pop(0)
            if len(self._rpc_calls) >= self._RPC_CALL_LIMIT:
                raise RuntimeError(
                    f"{self.chain_name} RPC rate limit exceeded "
                    f"({self._RPC_CALL_LIMIT} calls/{self._RPC_CALL_WINDOW}s)"
                )
            self._rpc_calls.append(now)

    async def _rpc_post(self, payload: dict, timeout: float | None = None) -> dict:
        """Post JSON-RPC to chain with fallback across multiple endpoints.

        Tries each RPC URL in order; returns first successful response.
        Raises the last encountered error if all fail.
        """
        if timeout is None:
            timeout = self._DEFAULT_TIMEOUT

        await self._check_rpc_rate_limit()

        last_error: Exception | None = None
        tag = f"[{self.chain_name}Verifier]"

        for rpc_url in self.rpc_urls:
            try:
                client = get_http_client()
                resp = await client.post(rpc_url, json=payload, timeout=timeout)
                # Guard against non-JSON or error HTTP responses
                if resp.status_code != 200 or not resp.text.strip().startswith("{"):
                    last_error = Exception(f"RPC {rpc_url}: HTTP {resp.status_code}")
                    continue
                data = resp.json()
                if "error" in data and data["error"]:
                    logger.warning(f"{tag} RPC {rpc_url} returned error: {data['error']}")
                    last_error = Exception(f"RPC error: {data['error']}")
                    continue
                return data
            except httpx.TimeoutException as exc:
                logger.warning(f"{tag} RPC {rpc_url} timeout: {exc}")
                last_error = exc
            except httpx.ConnectError as exc:
                logger.warning(f"{tag} RPC {rpc_url} connect error: {exc}")
                last_error = exc
            except Exception as exc:
                logger.warning(
                    f"{tag} RPC {rpc_url} unexpected error: {type(exc).__name__}: {exc}"
                )
                last_error = exc

        raise last_error or Exception(f"All {self.chain_name} RPC endpoints failed")

    # ------------------------------------------------------------------ #
    #  Transaction verification                                           #
    # ------------------------------------------------------------------ #

    async def verify_transaction(
        self, tx_hash: str, expected_to: Optional[str] = None
    ) -> dict:
        """Verify a transaction exists and succeeded on-chain.

        Retries up to _MAX_RETRIES times with exponential backoff if the receipt
        is not yet available (pending tx).

        Returns dict with "valid": True/False and chain metadata.
        """
        tag = f"[{self.chain_name}Verifier]"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
        }

        for attempt in range(self._MAX_RETRIES):
            try:
                data = await self._rpc_post(payload)
                result = data.get("result")
                if not result:
                    # Receipt not available yet -- wait and retry
                    await asyncio.sleep(2**attempt)
                    continue

                if result.get("status") != "0x1":
                    return {"valid": False, "error": "Transaction reverted"}

                if expected_to and result.get("to", "").lower() != expected_to.lower():
                    return {"valid": False, "error": "Recipient mismatch"}

                block_number = int(result.get("blockNumber", "0x0"), 16)
                gas_used = int(result.get("gasUsed", "0x0"), 16)

                logger.info(
                    f"{tag} TX verified: {tx_hash[:16]}... block={result.get('blockNumber')}"
                )
                return {
                    "valid": True,
                    "blockNumber": block_number,
                    "from": result.get("from", ""),
                    "to": result.get("to", ""),
                    "gasUsed": gas_used,
                    "network": self.network_id,
                    "chainId": self.chain_id,
                }

            except RuntimeError as exc:
                # Rate limit exceeded -- don't retry
                err = safe_error(exc, f"{self.chain_name.lower()}_verify_tx")
                err["valid"] = False
                return err
            except httpx.TimeoutException:
                logger.warning(
                    f"{tag} verify_transaction attempt {attempt + 1} timeout"
                )
                await asyncio.sleep(2**attempt)
            except httpx.ConnectError:
                logger.warning(
                    f"{tag} verify_transaction attempt {attempt + 1} connect error"
                )
                await asyncio.sleep(2**attempt)
            except Exception:
                logger.error(
                    f"{tag} verify_transaction attempt {attempt + 1} failed",
                    exc_info=True,
                )
                await asyncio.sleep(2**attempt)

        return {"valid": False, "error": "Verification failed after retries"}

    # ------------------------------------------------------------------ #
    #  USDC ERC-20 Transfer verification                                  #
    # ------------------------------------------------------------------ #

    async def verify_usdc_transfer(
        self,
        tx_hash: str,
        expected_amount_raw: Optional[int] = None,
        expected_recipient: Optional[str] = None,
    ) -> dict:
        """Verify a USDC ERC-20 Transfer event with recipient + amount check.

        1. Checks treasury config if no explicit recipient.
        2. Enforces min_tx_usdc threshold.
        3. Verifies the transaction succeeded.
        4. Parses Transfer logs for matching USDC contract + recipient + amount.
        """
        tag = f"[{self.chain_name}Verifier]"

        # Default to treasury
        if not expected_recipient:
            if not self.treasury_address:
                return {
                    "valid": False,
                    "error": f"Treasury not configured for {self.chain_name}",
                }
            expected_recipient = self.treasury_address

        # Min amount check
        if expected_amount_raw is not None and expected_amount_raw > 0:
            min_raw = int(self.min_tx_usdc * 1e6)
            if expected_amount_raw < min_raw:
                return {
                    "valid": False,
                    "error": (
                        f"Amount below minimum: "
                        f"${expected_amount_raw / 1e6:.4f} < ${self.min_tx_usdc}"
                    ),
                }

        # Step 1: Verify the transaction itself succeeded
        receipt = await self.verify_transaction(tx_hash, expected_to=None)
        if not receipt.get("valid"):
            return receipt

        # Step 2: Fetch logs and parse Transfer event
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
        }
        try:
            data = await self._rpc_post(payload)
            logs = data.get("result", {}).get("logs", [])

            for log_entry in logs:
                topics = log_entry.get("topics", [])

                # Must have at least 3 topics: event sig, from, to
                if len(topics) < 3:
                    continue
                # Must be from a recognized stablecoin contract and be a Transfer event
                if (
                    log_entry.get("address", "").lower() not in self.accepted_stablecoins
                    or topics[0] != _TRANSFER_TOPIC
                ):
                    continue
                # Validate topic lengths (address extraction safety)
                if len(topics[1]) < 42 or len(topics[2]) < 42:
                    logger.warning(
                        f"{tag} Malformed topics in tx {tx_hash}: "
                        f"len(topics[1])={len(topics[1])}, "
                        f"len(topics[2])={len(topics[2])}"
                    )
                    continue

                amount = int(log_entry.get("data", "0x0"), 16)
                from_addr = "0x" + topics[1][-40:]
                to_addr = "0x" + topics[2][-40:]

                # Verify recipient
                if (
                    expected_recipient
                    and to_addr.lower() != expected_recipient.lower()
                ):
                    return {
                        "valid": False,
                        "error": f"Recipient mismatch: {to_addr} != {expected_recipient}",
                    }

                # Verify amount
                if expected_amount_raw and amount < expected_amount_raw:
                    return {
                        "valid": False,
                        "error": (
                            f"Insufficient: {amount / 1e6:.2f} USDC "
                            f"< {expected_amount_raw / 1e6:.2f} USDC"
                        ),
                    }

                receipt["usdcTransfer"] = {
                    "from": from_addr,
                    "to": to_addr,
                    "amount_raw": amount,
                    "amount_usdc": amount / 1e6,
                }
                logger.info(
                    f"{tag} USDC transfer verified: {tx_hash[:16]}... "
                    f"{from_addr[:10]}...->{to_addr[:10]}... {amount / 1e6:.2f} USDC"
                )
                return receipt

            return {"valid": False, "error": "No USDC transfer found in logs"}

        except RuntimeError as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_verify_usdc")
            err["valid"] = False
            return err
        except httpx.TimeoutException as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_verify_usdc_timeout")
            err["valid"] = False
            return err
        except httpx.ConnectError as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_verify_usdc_connect")
            err["valid"] = False
            return err
        except Exception as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_verify_usdc")
            err["valid"] = False
            return err

    # ------------------------------------------------------------------ #
    #  x402 payment verification (facilitator + on-chain fallback)        #
    # ------------------------------------------------------------------ #

    async def x402_verify_payment(
        self,
        payment_header: str,
        expected_amount_usdc: float,
        facilitator_url: str = "",
    ) -> dict:
        """Verify an x402 payment via facilitator, falling back to direct on-chain.

        Args:
            payment_header: Payment payload or tx hash from the x402 header.
            expected_amount_usdc: Expected USDC amount.
            facilitator_url: x402 facilitator URL (e.g. https://x402.org/facilitator).
        """
        tag = f"[x402-{self.chain_name.lower()}]"

        # HTTPS warning
        if facilitator_url and not facilitator_url.startswith("https://"):
            logger.warning(f"{tag} Facilitator URL is not HTTPS: {facilitator_url}")

        # Try facilitator first
        if facilitator_url:
            try:
                client = get_http_client()
                resp = await client.post(
                    f"{facilitator_url}/verify",
                    json={
                        "paymentPayload": payment_header,
                        "network": self.network_id,
                        "expectedAmount": str(int(expected_amount_usdc * 1e6)),
                    },
                    timeout=self._DEFAULT_TIMEOUT,
                )
                result = resp.json()
                if resp.status_code == 200 and result.get("valid"):
                    logger.info(
                        f"{tag} Payment verified via facilitator: "
                        f"{result.get('txHash', '')[:16]}..."
                    )
                    return {
                        "valid": True,
                        "txHash": result.get("txHash", ""),
                        "network": self.network_id,
                        "settledAmount": result.get("settledAmount"),
                    }
                logger.warning(
                    f"{tag} Facilitator rejected: {result.get('error', 'unknown')}"
                )
            except httpx.TimeoutException as exc:
                logger.warning(f"{tag} Facilitator timeout: {exc}")
            except httpx.ConnectError as exc:
                logger.warning(f"{tag} Facilitator connect error: {exc}")
            except Exception as exc:
                logger.warning(
                    f"{tag} Facilitator error: {type(exc).__name__}: {exc}"
                )

        # Fallback: direct on-chain USDC verification if header looks like a tx hash
        if (
            payment_header
            and payment_header.startswith("0x")
            and len(payment_header) == 66
        ):
            logger.info(
                f"{tag} Attempting direct on-chain fallback for "
                f"{payment_header[:16]}..."
            )
            try:
                direct_result = await self.verify_usdc_transfer(
                    tx_hash=payment_header,
                    expected_amount_raw=int(expected_amount_usdc * 1e6),
                )
                if direct_result.get("valid"):
                    logger.info(
                        f"{tag} Direct on-chain verification succeeded for "
                        f"{payment_header[:16]}..."
                    )
                    direct_result["verifiedVia"] = "direct-onchain-fallback"
                    direct_result["txHash"] = payment_header
                    return direct_result
                logger.warning(
                    f"{tag} Direct on-chain fallback failed: "
                    f"{direct_result.get('error')}"
                )
            except Exception as exc:
                logger.error(
                    f"{tag} Direct on-chain fallback error: "
                    f"{type(exc).__name__}: {exc}"
                )

        return {
            "valid": False,
            "error": "Facilitator rejected and direct verification failed",
        }

    # ------------------------------------------------------------------ #
    #  x402 challenge builder                                             #
    # ------------------------------------------------------------------ #

    def build_x402_challenge(
        self,
        path: str,
        price_usdc: float,
        pay_to: str,
        facilitator_url: str = "",
    ) -> dict:
        """Build an x402 402-response payload for this chain.

        Args:
            path: API resource path being accessed.
            price_usdc: Price in USDC for the resource.
            pay_to: Address to receive payment.
            facilitator_url: x402 facilitator URL.
        """
        return {
            "scheme": "exact",
            "network": self.network_id,
            "maxAmountRequired": str(int(price_usdc * 1e6)),
            "resource": path,
            "description": f"MAXIA service: {path}",
            "mimeType": "application/json",
            "payTo": pay_to,
            "asset": self.usdc_contract,
            "maxTimeoutSeconds": 60,
            "extra": {
                "chainId": self.chain_id,
                "facilitator": facilitator_url,
            },
        }

    # ------------------------------------------------------------------ #
    #  Native token balance                                               #
    # ------------------------------------------------------------------ #

    async def get_native_balance(self, address: str) -> dict:
        """Get native token balance (ETH/MATIC/AVAX/BNB/SEI) for an address.

        Returns dict with balance in native token units (18 decimals).
        """
        tag = f"[{self.chain_name}Verifier]"

        if not address:
            return {"address": address, "error": "Address required"}

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBalance",
            "params": [address, "latest"],
        }
        try:
            data = await self._rpc_post(payload, timeout=10)
            balance_wei = int(data.get("result", "0x0"), 16)
            balance = balance_wei / 1e18

            return {
                "address": address,
                "balance_native": balance,
                "balance_wei": balance_wei,
                "chain": self.chain_name.lower(),
                "chainId": self.chain_id,
                "network": self.network_id,
            }
        except Exception as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_balance")
            err["address"] = address
            return err

    # ------------------------------------------------------------------ #
    #  Native value transfer verification                                 #
    # ------------------------------------------------------------------ #

    async def verify_native_transfer(
        self,
        tx_hash: str,
        expected_recipient: Optional[str] = None,
        min_native: Optional[float] = None,
    ) -> dict:
        """Verify a native token transfer (not ERC-20).

        Args:
            tx_hash: Transaction hash.
            expected_recipient: Expected recipient address.
            min_native: Minimum native token amount (in ETH/MATIC/etc units).
        """
        tag = f"[{self.chain_name}Verifier]"

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getTransactionByHash",
            "params": [tx_hash],
        }
        try:
            data = await self._rpc_post(payload)
            tx = data.get("result")
            if not tx:
                return {"valid": False, "error": "Transaction not found"}

            value_wei = int(tx.get("value", "0x0"), 16)
            value_native = value_wei / 1e18
            to_addr = tx.get("to", "")

            if (
                expected_recipient
                and to_addr.lower() != expected_recipient.lower()
            ):
                return {
                    "valid": False,
                    "error": f"Recipient mismatch: {to_addr}",
                }

            if min_native and value_native < min_native:
                return {
                    "valid": False,
                    "error": (
                        f"Insufficient: {value_native:.6f} "
                        f"< {min_native:.6f} (min)"
                    ),
                }

            # Confirm tx was actually successful
            receipt = await self.verify_transaction(tx_hash)
            if not receipt.get("valid"):
                return receipt

            receipt["nativeTransfer"] = {
                "from": tx.get("from", ""),
                "to": to_addr,
                "value_wei": value_wei,
                "value_native": value_native,
            }
            logger.info(
                f"{tag} Native transfer verified: {tx_hash[:16]}... "
                f"{value_native:.6f}"
            )
            return receipt

        except RuntimeError as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_verify_native")
            err["valid"] = False
            return err
        except httpx.TimeoutException as exc:
            err = safe_error(
                exc, f"{self.chain_name.lower()}_verify_native_timeout"
            )
            err["valid"] = False
            return err
        except httpx.ConnectError as exc:
            err = safe_error(
                exc, f"{self.chain_name.lower()}_verify_native_connect"
            )
            err["valid"] = False
            return err
        except Exception as exc:
            err = safe_error(exc, f"{self.chain_name.lower()}_verify_native")
            err["valid"] = False
            return err

    # ------------------------------------------------------------------ #
    #  Block number + contract logs (SCOUT agent support)                 #
    # ------------------------------------------------------------------ #

    async def get_block_number(self) -> int:
        """Get current block number for this chain."""
        try:
            data = await self._rpc_post(
                {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
                timeout=10,
            )
            return int(data.get("result", "0x0"), 16)
        except Exception as exc:
            logger.warning(
                f"[{self.chain_name}Verifier] get_block_number failed: {exc}"
            )
            return 0

    async def get_contract_logs(
        self,
        contract_address: str,
        from_block: Optional[str] = None,
        topic0: Optional[str] = None,
    ) -> list:
        """Get event logs for a contract (used by SCOUT for on-chain discovery).

        Args:
            contract_address: Contract to query logs for.
            from_block: Hex block number to start from (default: ~last 5000 blocks).
            topic0: Optional event signature filter.
        """
        if not from_block:
            current = await self.get_block_number()
            from_block = hex(max(0, current - 5000))

        params: dict = {
            "address": contract_address,
            "fromBlock": from_block,
            "toBlock": "latest",
        }
        if topic0:
            params["topics"] = [topic0]

        try:
            data = await self._rpc_post(
                {"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": [params]},
                timeout=30,
            )
            return data.get("result", [])
        except Exception as exc:
            logger.error(
                f"[{self.chain_name}Verifier] get_contract_logs error: "
                f"{type(exc).__name__}: {exc}"
            )
            return []

    async def get_wallet_tx_count(self, address: str) -> int:
        """Get transaction count (nonce) for a wallet -- indicates activity level."""
        try:
            data = await self._rpc_post(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getTransactionCount",
                    "params": [address, "latest"],
                },
                timeout=10,
            )
            return int(data.get("result", "0x0"), 16)
        except Exception as exc:
            logger.warning(
                f"[{self.chain_name}Verifier] get_wallet_tx_count failed: {exc}"
            )
            return 0

    # ------------------------------------------------------------------ #
    #  Chain info (for health/status endpoints)                           #
    # ------------------------------------------------------------------ #

    def get_chain_info(self) -> dict:
        """Return chain configuration info for health/debug endpoints."""
        return {
            "chain": self.chain_name,
            "chain_id": self.chain_id,
            "network": self.network_id,
            "usdc_contract": self.usdc_contract,
            "treasury": self.treasury_address or "(not configured)",
            "min_tx_usdc": self.min_tx_usdc,
            "rpc_count": len(self.rpc_urls),
        }

    def __repr__(self) -> str:
        return (
            f"EvmVerifier(chain={self.chain_name!r}, chain_id={self.chain_id}, "
            f"network={self.network_id!r}, rpcs={len(self.rpc_urls)})"
        )
