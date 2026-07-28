# Q2882: Registry lookup treats semantically different assets as one via Cross-Chain Actions Depend On / Wrong Lookup Would Not in MigrateChainConfigs

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with cross-chain actions that depend on enabled flags, decimals, or native-representation fields when a wrong lookup would not be caught by offchain honesty alone, and cause `MigrateChainConfigs` to trigger an unsafe state-transition edge case, so that it choose inputs that collide only after trimming or lowercasing in lookup paths, breaking the invariant that asset lookup must not collapse two real assets into one accounting bucket, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/migrations/v3/migrate.go::MigrateChainConfigs
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: cross-chain actions that depend on enabled flags, decimals, or native-representation fields
- Exploit idea: Cause `MigrateChainConfigs` to trigger an unsafe state-transition edge case, so it can choose inputs that collide only after trimming or lowercasing in lookup paths.
- Invariant to test: asset lookup must not collapse two real assets into one accounting bucket
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
