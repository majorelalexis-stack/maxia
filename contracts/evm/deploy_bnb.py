"""Deploy MaxiaEscrow on BNB Chain mainnet (chain 56).
Simplified version without OpenZeppelin (standalone, no imports needed).
Uses py-solc-x for compilation. Same contract as Base deployment.
"""
import json
import os
import sys

from web3 import Web3
from solcx import compile_source, set_solc_version

set_solc_version("0.8.20")

# ══════════════════════════════════════════
# Simplified Escrow contract (no OpenZeppelin dependency)
# ══════════════════════════════════════════

SOLIDITY_SOURCE = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract MaxiaEscrow {
    address public owner;
    IERC20 public usdc;
    address public treasury;
    uint256 public autoRefundDelay = 48 hours;

    uint256 public commissionBronze = 500;
    uint256 public commissionGold = 100;
    uint256 public commissionWhale = 10;
    uint256 public goldThreshold = 500e6;
    uint256 public whaleThreshold = 5000e6;

    enum Status { Locked, Confirmed, Disputed, Refunded, Settled }

    struct Escrow {
        address buyer;
        address seller;
        uint256 amount;
        uint256 commission;
        uint256 sellerGets;
        uint256 lockedAt;
        Status status;
        string serviceId;
    }

    mapping(bytes32 => Escrow) public escrows;
    mapping(address => uint256) public buyerVolume;

    uint256 public totalEscrows;
    uint256 public totalVolume;
    uint256 public totalCommissions;

    event EscrowCreated(bytes32 indexed escrowId, address indexed buyer, address indexed seller, uint256 amount);
    event EscrowConfirmed(bytes32 indexed escrowId, uint256 sellerGets, uint256 commission);
    event EscrowRefunded(bytes32 indexed escrowId, address indexed buyer, uint256 amount);
    event EscrowDisputed(bytes32 indexed escrowId, address indexed initiator);
    event EscrowSettled(bytes32 indexed escrowId, address indexed winner, uint256 amount);

    modifier onlyOwner() { require(msg.sender == owner, "Not owner"); _; }

    constructor(address _usdc, address _treasury) {
        require(_usdc != address(0) && _treasury != address(0), "Invalid address");
        owner = msg.sender;
        usdc = IERC20(_usdc);
        treasury = _treasury;
    }

    function _getCommissionBps(address buyer) internal view returns (uint256) {
        uint256 vol = buyerVolume[buyer];
        if (vol >= whaleThreshold) return commissionWhale;
        if (vol >= goldThreshold) return commissionGold;
        return commissionBronze;
    }

    function lockEscrow(address seller, uint256 amount, string calldata serviceId) external returns (bytes32) {
        require(seller != address(0) && seller != msg.sender, "Invalid seller");
        require(amount > 0, "Amount must be > 0");

        uint256 commBps = _getCommissionBps(msg.sender);
        uint256 commission = (amount * commBps) / 10000;
        uint256 sellerGets = amount - commission;

        bytes32 escrowId = keccak256(abi.encodePacked(msg.sender, seller, amount, block.timestamp, totalEscrows));

        require(usdc.transferFrom(msg.sender, address(this), amount), "USDC transfer failed");

        escrows[escrowId] = Escrow(msg.sender, seller, amount, commission, sellerGets, block.timestamp, Status.Locked, serviceId);
        totalEscrows++;
        totalVolume += amount;
        buyerVolume[msg.sender] += amount;

        emit EscrowCreated(escrowId, msg.sender, seller, amount);
        return escrowId;
    }

    function confirmDelivery(bytes32 escrowId) external {
        Escrow storage e = escrows[escrowId];
        require(e.buyer == msg.sender, "Only buyer");
        require(e.status == Status.Locked, "Not locked");

        e.status = Status.Confirmed;
        require(usdc.transfer(e.seller, e.sellerGets), "Seller transfer failed");
        if (e.commission > 0) {
            require(usdc.transfer(treasury, e.commission), "Commission transfer failed");
            totalCommissions += e.commission;
        }
        emit EscrowConfirmed(escrowId, e.sellerGets, e.commission);
    }

    function autoRefund(bytes32 escrowId) external {
        Escrow storage e = escrows[escrowId];
        require(e.status == Status.Locked, "Not locked");
        require(block.timestamp >= e.lockedAt + autoRefundDelay, "Too early");

        e.status = Status.Refunded;
        require(usdc.transfer(e.buyer, e.amount), "Refund failed");
        emit EscrowRefunded(escrowId, e.buyer, e.amount);
    }

    function openDispute(bytes32 escrowId) external {
        Escrow storage e = escrows[escrowId];
        require(e.status == Status.Locked, "Not locked");
        require(msg.sender == e.buyer || msg.sender == e.seller, "Not party");
        e.status = Status.Disputed;
        emit EscrowDisputed(escrowId, msg.sender);
    }

    function settleDispute(bytes32 escrowId, address winner) external onlyOwner {
        Escrow storage e = escrows[escrowId];
        require(e.status == Status.Disputed, "Not disputed");
        require(winner == e.buyer || winner == e.seller, "Invalid winner");

        e.status = Status.Settled;
        if (winner == e.buyer) {
            require(usdc.transfer(e.buyer, e.amount), "Transfer failed");
        } else {
            require(usdc.transfer(e.seller, e.sellerGets), "Transfer failed");
            if (e.commission > 0) {
                require(usdc.transfer(treasury, e.commission), "Commission failed");
                totalCommissions += e.commission;
            }
        }
        emit EscrowSettled(escrowId, winner, e.amount);
    }

    function getCommissionTier(address buyer) external view returns (string memory tier, uint256 bps) {
        uint256 vol = buyerVolume[buyer];
        if (vol >= whaleThreshold) return ("WHALE", commissionWhale);
        if (vol >= goldThreshold) return ("GOLD", commissionGold);
        return ("BRONZE", commissionBronze);
    }

    function updateTreasury(address _treasury) external onlyOwner {
        require(_treasury != address(0), "Invalid");
        treasury = _treasury;
    }

    function updateCommissions(uint256 _bronze, uint256 _gold, uint256 _whale) external onlyOwner {
        require(_bronze <= 1000 && _gold <= 500 && _whale <= 100, "Too high");
        commissionBronze = _bronze;
        commissionGold = _gold;
        commissionWhale = _whale;
    }

    function getStats() external view returns (uint256, uint256, uint256) {
        return (totalEscrows, totalVolume, totalCommissions);
    }
}
"""

# ══════════════════════════════════════════
# Configuration — BNB Chain mainnet (chain 56)
# ══════════════════════════════════════════

CHAIN_NAME = "BNB"
CHAIN_ID = 56
RPC_URL = "https://bsc-dataseed.binance.org"
USDC_ADDRESS = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"
EXPLORER_URL = "https://bscscan.com"
NATIVE_TOKEN = "BNB"

PRIVATE_KEY = os.getenv("BNB_DEPLOYER_PRIVKEY", os.getenv("PRIVATE_KEY", ""))
TREASURY_ADDRESS = os.getenv("TREASURY_ADDRESS", "")

if not PRIVATE_KEY:
    print("ERROR: Set BNB_DEPLOYER_PRIVKEY or PRIVATE_KEY env var")
    sys.exit(1)

# ══════════════════════════════════════════
# Connect & validate
# ══════════════════════════════════════════

w3 = Web3(Web3.HTTPProvider(RPC_URL))

if not w3.is_connected():
    print(f"ERROR: Cannot connect to {CHAIN_NAME} Chain RPC at {RPC_URL}")
    sys.exit(1)

account = w3.eth.account.from_key(PRIVATE_KEY)
DEPLOYER = account.address

# Use deployer as treasury if TREASURY_ADDRESS not set
treasury = Web3.to_checksum_address(TREASURY_ADDRESS) if TREASURY_ADDRESS else DEPLOYER

chain_id = w3.eth.chain_id
if chain_id != CHAIN_ID:
    print(f"ERROR: Expected chain ID {CHAIN_ID}, got {chain_id}")
    sys.exit(1)

print(f"Chain: {CHAIN_NAME} Chain (ID: {chain_id})")
print(f"Deployer: {DEPLOYER}")
print(f"Treasury: {treasury}")
balance = w3.eth.get_balance(DEPLOYER)
print(f"Balance: {w3.from_wei(balance, 'ether')} {NATIVE_TOKEN}")

if balance == 0:
    print(f"ERROR: No {NATIVE_TOKEN} for gas!")
    sys.exit(1)

# ══════════════════════════════════════════
# Compile
# ══════════════════════════════════════════

print("\nCompiling...")
compiled = compile_source(SOLIDITY_SOURCE, output_values=["abi", "bin"])
contract_id, contract_interface = compiled.popitem()
abi = contract_interface["abi"]
bytecode = contract_interface["bin"]
print(f"Compiled: {len(bytecode)} bytes")

# ══════════════════════════════════════════
# Deploy
# ══════════════════════════════════════════

print(f"\nDeploying MaxiaEscrow on {CHAIN_NAME} Chain mainnet...")
contract = w3.eth.contract(abi=abi, bytecode=bytecode)
tx = contract.constructor(
    Web3.to_checksum_address(USDC_ADDRESS),
    treasury,
).build_transaction({
    "from": DEPLOYER,
    "nonce": w3.eth.get_transaction_count(DEPLOYER),
    "gas": 2_000_000,
    "gasPrice": w3.eth.gas_price,
    "chainId": CHAIN_ID,
})

signed = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
print(f"TX sent: {tx_hash.hex()}")

receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
contract_address = receipt.contractAddress

print(f"\n{'='*50}")
print(f"CONTRACT DEPLOYED!")
print(f"Chain: {CHAIN_NAME} Chain (ID: {CHAIN_ID})")
print(f"Address: {contract_address}")
print(f"TX: {EXPLORER_URL}/tx/{tx_hash.hex()}")
print(f"Contract: {EXPLORER_URL}/address/{contract_address}")
print(f"Gas used: {receipt.gasUsed}")
print(f"Status: {'SUCCESS' if receipt.status == 1 else 'FAILED'}")
print(f"{'='*50}")

# ══════════════════════════════════════════
# Save ABI + deployed address
# ══════════════════════════════════════════

abi_path = f"MaxiaEscrow_{CHAIN_NAME.lower()}_abi.json"
with open(abi_path, "w") as f:
    json.dump(abi, f, indent=2)
print(f"\nABI saved to {abi_path}")

env_path = f".env.{CHAIN_NAME.lower()}"
with open(env_path, "w") as f:
    f.write(f"# MaxiaEscrow deployed on {CHAIN_NAME} Chain mainnet\n")
    f.write(f"BNB_ESCROW_ADDRESS={contract_address}\n")
    f.write(f"BNB_ESCROW_TX={tx_hash.hex()}\n")
    f.write(f"BNB_USDC_ADDRESS={USDC_ADDRESS}\n")
    f.write(f"BNB_CHAIN_ID={CHAIN_ID}\n")
print(f"Deployed address saved to {env_path}")
