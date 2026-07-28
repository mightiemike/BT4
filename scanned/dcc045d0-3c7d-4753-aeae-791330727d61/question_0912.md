# Q0912: Enabled-flag interpretation strands a live user flow via Address Caip-2 Formatting Variants / Registry-Derived Semantics Affect Mint, in MigrateChainConfigs

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when registry-derived semantics affect mint, refund, revert, or outbound routing, and cause `MigrateChainConfigs` to trigger an unsafe state-transition edge case, so that it make a user action pass one enablement gate and later fail under a different interpretation of the same config, breaking the invariant that enabled flags must produce one consistent allow/deny decision across the full lifecycle, and resulting in Permanent freezing of funds?

## Target
- File/function: x/uregistry/migrations/v3/migrate.go::MigrateChainConfigs
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `MigrateChainConfigs` to trigger an unsafe state-transition edge case, so it can make a user action pass one enablement gate and later fail under a different interpretation of the same config.
- Invariant to test: enabled flags must produce one consistent allow/deny decision across the full lifecycle
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
