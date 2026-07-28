# Q2691: Gateway or vault method selection misroutes user value via Address Caip-2 Formatting Variants / Registry-Derived Semantics Affect Mint, in NativeRepresentation.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when registry-derived semantics affect mint, refund, revert, or outbound routing, and cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it make registry-derived method metadata point a live flow at the wrong destination semantics, breaking the invariant that registry-selected methods must remain bound to the intended chain and asset only, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/types/native_representation.go::NativeRepresentation.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can make registry-derived method metadata point a live flow at the wrong destination semantics.
- Invariant to test: registry-selected methods must remain bound to the intended chain and asset only
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
