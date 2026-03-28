# Smart Contract Oracle Upgrade Plan

## Status: PREPARED — waiting for SOL funding

## Cost Estimate
- **Solana upgrade**: ~0.5 SOL (program account rent increase + tx fee)
- **Base new contract**: ~$0.10 ETH (L2 gas)
- **Wallet VPS current**: 0.31 SOL — **need ~2.2 SOL more** (also for security.txt)
- **Chainlink**: FREE (public on-chain reads)

## Solana Escrow — Pyth Price Verification

### What changes
Add optional Pyth price verification to `create_escrow`:
- New dependency: `pyth-solana-receiver-sdk`
- New account in CreateEscrow: `price_feed` (optional, Pyth price account)
- Before locking USDC, verify the service price against Pyth oracle
- Log the oracle price + confidence at escrow creation time
- Reject if price is stale (>120s) or confidence >2%

### Cargo.toml addition
```toml
pyth-solana-receiver-sdk = "0.4"
```

### Code change (create_escrow)
```rust
// Optional: verify price against Pyth oracle
if let Some(price_feed) = ctx.accounts.price_feed.as_ref() {
    let price_data = pyth_solana_receiver_sdk::price_update::PriceUpdateV2::try_deserialize(
        &mut &price_feed.data.borrow()[..]
    )?;
    let price = price_data.get_price_no_older_than(
        &Clock::get()?,
        120, // max 120s stale
        &feed_id,
    )?;
    // Store oracle price snapshot in escrow for audit trail
    escrow.oracle_price = price.price;
    escrow.oracle_conf = price.conf;
    escrow.oracle_ts = price.publish_time;
}
```

### Migration
- Program upgrade via `anchor upgrade` (same program ID)
- Config account compatible (no schema change)
- Existing escrows unaffected (price_feed is optional)

## Base Escrow — Chainlink Price Verification

### What changes
Deploy NEW contract (current one is immutable) with Chainlink integration:
- Import `AggregatorV3Interface`
- Before locking USDC, read ETH/USD or BTC/USD price from Chainlink
- Store oracle price at escrow creation
- Reject if Chainlink price is stale (>3600s)

### Solidity addition
```solidity
import "@chainlink/contracts/src/v0.8/interfaces/AggregatorV3Interface.sol";

AggregatorV3Interface public ethUsdFeed;

constructor(address _usdc, address _ethUsdFeed) {
    usdcToken = IERC20(_usdc);
    ethUsdFeed = AggregatorV3Interface(_ethUsdFeed);
}

function _getChainlinkPrice() internal view returns (int256 price, uint256 updatedAt) {
    (, price,, updatedAt,) = ethUsdFeed.latestRoundData();
    require(block.timestamp - updatedAt < 3600, "Chainlink price stale");
    require(price > 0, "Invalid price");
}
```

### Migration
- Deploy new contract on Base mainnet
- Update `base_escrow_client.py` with new contract address
- Old contract remains (existing escrows settle normally)
- Frontend updated to point to new contract

## Steps to Execute (when funded)

1. Fund wallet with 2.7 SOL total
2. `cd contracts/programs/maxia_escrow && anchor build`
3. `anchor upgrade 8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY --program-filepath target/deploy/maxia_escrow.so`
4. Verify: `anchor verify 8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY`
5. Deploy Base contract: `python3 deploy_base_v2.py`
6. Update backend with new Base contract address
7. Test both chains
