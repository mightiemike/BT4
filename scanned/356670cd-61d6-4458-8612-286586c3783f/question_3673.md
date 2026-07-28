# Q3673: Token-address canonicalization points to the wrong whitelist entry via Cross-Chain Actions Depend On / Live Flow Will Look in ChainEnabled.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with cross-chain actions that depend on enabled flags, decimals, or native-representation fields when the live flow will look up chain and token config before moving value, and cause `ChainEnabled.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it use address-formatting variants to make one token flow use another token's config, breaking the invariant that one external token should map to exactly one canonical config regardless of formatting, and resulting in Direct theft/loss or wrong-party minting?

## Target
- File/function: x/uregistry/types/chain_enabled.go::ChainEnabled.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: cross-chain actions that depend on enabled flags, decimals, or native-representation fields
- Exploit idea: Cause `ChainEnabled.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can use address-formatting variants to make one token flow use another token's config.
- Invariant to test: one external token should map to exactly one canonical config regardless of formatting
- Expected Immunefi impact: Direct theft/loss or wrong-party minting
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
