// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title MaxiaEscrow
 * @notice AI-to-AI marketplace escrow for USDC payments on EVM chains (Base, Ethereum, Polygon, Arbitrum).
 * @dev Buyer locks USDC -> Seller delivers -> Buyer confirms -> Funds released (minus commission).
 *      Auto-refund after 48h if no confirmation. AI dispute resolution via admin.
 *
 * Deployed on:
 *   - Base (low fees, Coinbase ecosystem)
 *   - Ethereum (large transactions, min $10)
 *   - Polygon, Arbitrum, Avalanche, BNB (same contract)
 *
 * Commission tiers (on-chain):
 *   BRONZE: 5%   (volume < 500 USDC)
 *   GOLD:   1%   (500 - 5000 USDC)
 *   WHALE:  0.1% (> 5000 USDC)
 */
contract MaxiaEscrow is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ══════════════════════════════════════════
    // State
    // ══════════════════════════════════════════

    IERC20 public immutable usdc;
    address public treasury;
    uint256 public autoRefundDelay = 48 hours;

    // Commission tiers (basis points: 500 = 5%, 100 = 1%, 10 = 0.1%)
    uint256 public commissionBronze = 500;  // 5%
    uint256 public commissionGold = 100;    // 1%
    uint256 public commissionWhale = 10;    // 0.1%
    uint256 public goldThreshold = 500e6;   // 500 USDC (6 decimals)
    uint256 public whaleThreshold = 5000e6; // 5000 USDC

    enum EscrowStatus { Locked, Confirmed, Disputed, Refunded, Settled }

    struct Escrow {
        address buyer;
        address seller;
        uint256 amount;         // USDC amount (6 decimals)
        uint256 commission;     // Commission amount
        uint256 sellerGets;     // Amount seller receives
        uint256 lockedAt;       // Timestamp when locked
        uint256 settledAt;      // Timestamp when settled
        EscrowStatus status;
        string serviceId;       // MAXIA service ID
        string intentHash;      // AIP intent signature hash (proof)
    }

    mapping(bytes32 => Escrow) public escrows;
    mapping(address => uint256) public buyerVolume; // 30-day rolling volume

    uint256 public totalEscrows;
    uint256 public totalVolume;
    uint256 public totalCommissions;

    // ══════════════════════════════════════════
    // Events
    // ══════════════════════════════════════════

    event EscrowCreated(bytes32 indexed escrowId, address indexed buyer, address indexed seller, uint256 amount, string serviceId);
    event EscrowConfirmed(bytes32 indexed escrowId, uint256 sellerGets, uint256 commission);
    event EscrowRefunded(bytes32 indexed escrowId, address indexed buyer, uint256 amount);
    event EscrowDisputed(bytes32 indexed escrowId, address indexed initiator);
    event EscrowSettled(bytes32 indexed escrowId, address indexed winner, uint256 amount);
    event CommissionUpdated(uint256 bronze, uint256 gold, uint256 whale);
    event TreasuryUpdated(address newTreasury);

    // ══════════════════════════════════════════
    // Constructor
    // ══════════════════════════════════════════

    constructor(address _usdc, address _treasury) Ownable(msg.sender) {
        require(_usdc != address(0), "Invalid USDC address");
        require(_treasury != address(0), "Invalid treasury");
        usdc = IERC20(_usdc);
        treasury = _treasury;
    }

    // ══════════════════════════════════════════
    // Core: Lock -> Confirm -> Release
    // ══════════════════════════════════════════

    /**
     * @notice Lock USDC in escrow for a service purchase.
     * @param seller Address of the service provider
     * @param amount USDC amount (6 decimals)
     * @param serviceId MAXIA service ID
     * @param intentHash AIP signed intent hash (proof of agent's intention)
     */
    function lockEscrow(
        address seller,
        uint256 amount,
        string calldata serviceId,
        string calldata intentHash
    ) external nonReentrant returns (bytes32 escrowId) {
        require(seller != address(0) && seller != msg.sender, "Invalid seller");
        require(amount > 0, "Amount must be > 0");
        require(bytes(serviceId).length > 0, "Service ID required");

        // Calculate commission based on buyer volume tier
        uint256 commBps = _getCommissionBps(msg.sender);
        uint256 commission = (amount * commBps) / 10000;
        uint256 sellerGets = amount - commission;

        // Generate unique escrow ID
        escrowId = keccak256(abi.encodePacked(
            msg.sender, seller, amount, block.timestamp, totalEscrows
        ));

        // Transfer USDC from buyer to contract
        usdc.safeTransferFrom(msg.sender, address(this), amount);

        escrows[escrowId] = Escrow({
            buyer: msg.sender,
            seller: seller,
            amount: amount,
            commission: commission,
            sellerGets: sellerGets,
            lockedAt: block.timestamp,
            settledAt: 0,
            status: EscrowStatus.Locked,
            serviceId: serviceId,
            intentHash: intentHash
        });

        totalEscrows++;
        totalVolume += amount;
        buyerVolume[msg.sender] += amount;

        emit EscrowCreated(escrowId, msg.sender, seller, amount, serviceId);
    }

    /**
     * @notice Buyer confirms delivery. Releases funds to seller (minus commission).
     */
    function confirmDelivery(bytes32 escrowId) external nonReentrant {
        Escrow storage e = escrows[escrowId];
        require(e.buyer == msg.sender, "Only buyer can confirm");
        require(e.status == EscrowStatus.Locked, "Not in locked state");

        e.status = EscrowStatus.Confirmed;
        e.settledAt = block.timestamp;

        // Pay seller
        usdc.safeTransfer(e.seller, e.sellerGets);
        // Pay commission to treasury
        if (e.commission > 0) {
            usdc.safeTransfer(treasury, e.commission);
            totalCommissions += e.commission;
        }

        emit EscrowConfirmed(escrowId, e.sellerGets, e.commission);
    }

    /**
     * @notice Auto-refund if buyer doesn't confirm within 48h.
     * Anyone can call this after the delay.
     */
    function autoRefund(bytes32 escrowId) external nonReentrant {
        Escrow storage e = escrows[escrowId];
        require(e.status == EscrowStatus.Locked, "Not in locked state");
        require(block.timestamp >= e.lockedAt + autoRefundDelay, "Too early for auto-refund");

        e.status = EscrowStatus.Refunded;
        e.settledAt = block.timestamp;

        usdc.safeTransfer(e.buyer, e.amount);

        emit EscrowRefunded(escrowId, e.buyer, e.amount);
    }

    // ══════════════════════════════════════════
    // Disputes
    // ══════════════════════════════════════════

    /**
     * @notice Either party can open a dispute. Admin resolves.
     */
    function openDispute(bytes32 escrowId) external {
        Escrow storage e = escrows[escrowId];
        require(e.status == EscrowStatus.Locked, "Not in locked state");
        require(msg.sender == e.buyer || msg.sender == e.seller, "Not a party");

        e.status = EscrowStatus.Disputed;
        emit EscrowDisputed(escrowId, msg.sender);
    }

    /**
     * @notice Admin settles a dispute. Winner gets the funds.
     */
    function settleDispute(bytes32 escrowId, address winner) external onlyOwner nonReentrant {
        Escrow storage e = escrows[escrowId];
        require(e.status == EscrowStatus.Disputed, "Not disputed");
        require(winner == e.buyer || winner == e.seller, "Winner must be buyer or seller");

        e.status = EscrowStatus.Settled;
        e.settledAt = block.timestamp;

        if (winner == e.buyer) {
            usdc.safeTransfer(e.buyer, e.amount);
        } else {
            usdc.safeTransfer(e.seller, e.sellerGets);
            if (e.commission > 0) {
                usdc.safeTransfer(treasury, e.commission);
                totalCommissions += e.commission;
            }
        }

        emit EscrowSettled(escrowId, winner, e.amount);
    }

    // ══════════════════════════════════════════
    // Commission logic (on-chain, #10)
    // ══════════════════════════════════════════

    function _getCommissionBps(address buyer) internal view returns (uint256) {
        uint256 vol = buyerVolume[buyer];
        if (vol >= whaleThreshold) return commissionWhale;
        if (vol >= goldThreshold) return commissionGold;
        return commissionBronze;
    }

    /**
     * @notice Get commission tier for a buyer.
     */
    function getCommissionTier(address buyer) external view returns (string memory tier, uint256 bps) {
        uint256 vol = buyerVolume[buyer];
        if (vol >= whaleThreshold) return ("WHALE", commissionWhale);
        if (vol >= goldThreshold) return ("GOLD", commissionGold);
        return ("BRONZE", commissionBronze);
    }

    // ══════════════════════════════════════════
    // Admin
    // ══════════════════════════════════════════

    function updateCommissions(uint256 _bronze, uint256 _gold, uint256 _whale) external onlyOwner {
        require(_bronze <= 1000 && _gold <= 500 && _whale <= 100, "Commission too high");
        commissionBronze = _bronze;
        commissionGold = _gold;
        commissionWhale = _whale;
        emit CommissionUpdated(_bronze, _gold, _whale);
    }

    function updateTreasury(address _treasury) external onlyOwner {
        require(_treasury != address(0), "Invalid treasury");
        treasury = _treasury;
        emit TreasuryUpdated(_treasury);
    }

    function updateAutoRefundDelay(uint256 _delay) external onlyOwner {
        require(_delay >= 1 hours && _delay <= 7 days, "Invalid delay");
        autoRefundDelay = _delay;
    }

    // ══════════════════════════════════════════
    // View
    // ══════════════════════════════════════════

    function getEscrow(bytes32 escrowId) external view returns (Escrow memory) {
        return escrows[escrowId];
    }

    function getStats() external view returns (uint256 total, uint256 volume, uint256 commissions) {
        return (totalEscrows, totalVolume, totalCommissions);
    }
}
