# Q0323: Chain-id canonicalization resolves the wrong config via Cross-Chain Actions Depend On / Wrong Lookup Would Not in ChainConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with cross-chain actions that depend on enabled flags, decimals, or native-representation fields when a wrong lookup would not be caught by offchain honesty alone, and cause `ChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it format a chain id so execution consumes another chain's enabled flags, gateway settings, or confirmations, breaking the invariant that each external chain id must bind to exactly one canonical config for execution, and resulting in Direct loss or permanent freeze of funds?

## Target
- File/function: x/uregistry/types/chain_config.go::ChainConfig.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: cross-chain actions that depend on enabled flags, decimals, or native-representation fields
- Exploit idea: Cause `ChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can format a chain id so execution consumes another chain's enabled flags, gateway settings, or confirmations.
- Invariant to test: each external chain id must bind to exactly one canonical config for execution
- Expected Immunefi impact: Direct loss or permanent freeze of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
