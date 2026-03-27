# MAXIA Escrow — SUI (Move)

## Overview
Escrow smart contract for SUI blockchain using Move language.
Handles USDC locking, confirmation, auto-refund, disputes.

## SUI Specifics
- Language: Move (Sui variant, object-centric)
- Token: USDC native on SUI (Circle deployment)
- Gas: ~0.001 SUI per transaction
- Finality: ~400ms (fastest of all chains)
- Object model: each escrow = a Sui Object (owned, transferable)

## Contract Design (Move module)
```move
module maxia::escrow {
    struct Escrow has key, store {
        id: UID,
        buyer: address,
        seller: address,
        amount: u64,
        commission: u64,
        seller_gets: u64,
        locked_at: u64,
        status: u8,  // 0=locked, 1=confirmed, 2=disputed, 3=refunded, 4=settled
        service_id: String,
        intent_hash: String,
    }

    public entry fun lock_escrow(buyer, seller, amount, service_id, intent_hash, coin: Coin<USDC>, ctx)
    public entry fun confirm_delivery(escrow: &mut Escrow, ctx)
    public entry fun auto_refund(escrow: &mut Escrow, clock: &Clock, ctx)
    public entry fun open_dispute(escrow: &mut Escrow, ctx)
    public entry fun settle_dispute(escrow: &mut Escrow, winner: address, ctx)  // admin only
}
```

## Commission (on-chain)
- BRONZE: 5% (< 500 USDC volume)
- GOLD: 1% (500-5000 USDC)
- WHALE: 0.1% (> 5000 USDC)

## Dependencies
- sui-cli, Move compiler
- USDC Coin type on SUI mainnet

## Deploy
```bash
sui client publish --gas-budget 100000000
```

## Status: SPEC ONLY — Implementation next session
