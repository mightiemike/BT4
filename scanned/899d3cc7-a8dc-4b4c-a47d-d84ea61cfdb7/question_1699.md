# Q1699: Reverse PRC20 lookup resolves the wrong source asset via Address Caip-2 Formatting Variants / Wrong Lookup Would Not in MigrateChainConfigs

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when a wrong lookup would not be caught by offchain honesty alone, and cause `MigrateChainConfigs` to trigger an unsafe state-transition edge case, so that it make a user flow that should map one PRC20 back to another source-chain asset, breaking the invariant that one PRC20 address must resolve to exactly one canonical external asset configuration, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/migrations/v2/migrate.go::MigrateChainConfigs
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `MigrateChainConfigs` to trigger an unsafe state-transition edge case, so it can make a user flow that should map one PRC20 back to another source-chain asset.
- Invariant to test: one PRC20 address must resolve to exactly one canonical external asset configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
