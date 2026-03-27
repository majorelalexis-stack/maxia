# MAXIA Escrow — TRON (Solidity TVM)

## Overview
Escrow smart contract for TRON blockchain using Solidity (TVM variant).
Almost identical to EVM contract — TRON uses Solidity but compiles to TVM.

## TRON Specifics
- Language: Solidity (TVM-compatible, same as EVM with minor differences)
- Token: USDT-TRC20 (biggest stablecoin on TRON) + USDC-TRC20
- Gas: Energy + Bandwidth (very cheap, ~$0.01)
- Finality: ~3 seconds
- Address format: Base58 (starts with T)

## Contract
Same as `contracts/evm/MaxiaEscrow.sol` with minor TVM adaptations:
- Use TRC-20 interface instead of ERC-20 (same API, different address format)
- `address` type works the same
- OpenZeppelin contracts compatible via tron-solidity

## Deploy
```bash
# Using TronBox (Truffle for TRON)
tronbox compile
tronbox migrate --network mainnet
```

## USDC/USDT addresses on TRON
- USDT: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
- USDC: TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8

## Commission (on-chain)
Same tiers as EVM contract.

## Status: SPEC ONLY — Can reuse MaxiaEscrow.sol with TronBox
