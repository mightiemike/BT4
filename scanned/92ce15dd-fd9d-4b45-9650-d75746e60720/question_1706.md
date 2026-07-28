# Q1706: Reverse PRC20 lookup resolves the wrong source asset via Cross-Chain Actions Depend On / Same Asset May Appear in NativeRepresentation.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with cross-chain actions that depend on enabled flags, decimals, or native-representation fields when the same asset may appear in multiple encodings or address formats, and cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it make a user flow that should map one PRC20 back to another source-chain asset, breaking the invariant that one PRC20 address must resolve to exactly one canonical external asset configuration, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/native_representation.go::NativeRepresentation.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: cross-chain actions that depend on enabled flags, decimals, or native-representation fields
- Exploit idea: Cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can make a user flow that should map one PRC20 back to another source-chain asset.
- Invariant to test: one PRC20 address must resolve to exactly one canonical external asset configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
