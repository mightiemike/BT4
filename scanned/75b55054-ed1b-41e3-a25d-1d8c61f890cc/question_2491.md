# Q2491: Enabled-flag interpretation strands a live user flow via Address Caip-2 Formatting Variants / Wrong Lookup Would Not in ChainEnabled.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when a wrong lookup would not be caught by offchain honesty alone, and cause `ChainEnabled.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it make a user action pass one enablement gate and later fail under a different interpretation of the same config, breaking the invariant that enabled flags must produce one consistent allow/deny decision across the full lifecycle, and resulting in Permanent freezing of funds?

## Target
- File/function: x/uregistry/types/chain_enabled.go::ChainEnabled.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `ChainEnabled.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can make a user action pass one enablement gate and later fail under a different interpretation of the same config.
- Invariant to test: enabled flags must produce one consistent allow/deny decision across the full lifecycle
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
