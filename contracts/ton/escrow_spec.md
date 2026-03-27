# MAXIA Escrow — TON (FunC)

## Overview
Escrow smart contract for TON blockchain using FunC language.
Handles USDC (jUSDC on TON) locking, confirmation, auto-refund, disputes.

## TON Specifics
- Language: FunC (compiled to TVM bytecode)
- Token standard: TEP-74 (Jetton, TON's ERC-20 equivalent)
- USDC on TON: jUSDC via TON Bridge (Circle partnership)
- Gas: ~0.01 TON per transaction ($0.005)
- Finality: ~5 seconds

## Contract Design
```
contract maxia_escrow {
    storage: owner, treasury, usdc_jetton_master, escrow_count

    messages:
        lock_escrow(buyer, seller, amount, service_id, intent_hash)
        confirm_delivery(escrow_id)
        auto_refund(escrow_id)  // after 48h
        open_dispute(escrow_id)
        settle_dispute(escrow_id, winner)  // owner only
}
```

## Commission (on-chain)
- BRONZE: 5% (< 500 USDC volume)
- GOLD: 1% (500-5000 USDC)
- WHALE: 0.1% (> 5000 USDC)

## Dependencies
- ton-sdk, toncli for deployment
- jUSDC Jetton Master address on TON mainnet

## Deploy
```bash
toncli deploy --network mainnet maxia_escrow.fc
```

## Status: SPEC ONLY — Implementation next session
