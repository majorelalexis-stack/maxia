#!/usr/bin/env node
/**
 * Deploy MAXIA Hub R3 EAS schema on Base mainnet.
 *
 * Usage:
 *   npm install @ethereum-attestation-service/eas-sdk ethers
 *   PRIVATE_KEY=0x... node scripts/deploy_eas_schema.js
 *
 * Copy the printed schema UID into VPS .env:
 *   EAS_MAXIA_SCHEMA_ID=0x<uid>
 */

const { SchemaRegistry } = require("@ethereum-attestation-service/eas-sdk");
const { ethers } = require("ethers");

// Base mainnet
const RPC_URL = "https://mainnet.base.org";
const SCHEMA_REGISTRY_ADDRESS = "0xA7b39296258348C78294F95B872b282326A97BDF";

// MAXIA Hub R3 schema:
//   address agent    — wallet address of the attested agent
//   string  did      — DID identifier (did:maxia:<hub_id>)
//   uint256 score    — hub score 0-100 at attestation time
//   bool    verified — true if full KYC/on-chain check passed
const SCHEMA = "address agent,string did,uint256 score,bool verified";
const REVOCABLE = true;

async function main() {
  const pk = process.env.PRIVATE_KEY;
  if (!pk) {
    console.error("Set PRIVATE_KEY env var");
    process.exit(1);
  }

  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const signer = new ethers.Wallet(pk, provider);
  console.log("Deployer:", signer.address);

  const registry = new SchemaRegistry(SCHEMA_REGISTRY_ADDRESS);
  registry.connect(signer);

  console.log("Registering schema:", SCHEMA);
  const tx = await registry.register({ schema: SCHEMA, revocable: REVOCABLE });
  const uid = await tx.wait();

  console.log("\n=== SUCCESS ===");
  console.log("Schema UID:", uid);
  console.log("\nAdd to VPS .env:");
  console.log(`EAS_MAXIA_SCHEMA_ID=${uid}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
