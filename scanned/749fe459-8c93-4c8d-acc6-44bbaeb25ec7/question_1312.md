# Q1312: Registry lookup treats semantically different assets as one via Address Caip-2 Formatting Variants / Live Flow Will Look in NativeRepresentation.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when the live flow will look up chain and token config before moving value, and cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it choose inputs that collide only after trimming or lowercasing in lookup paths, breaking the invariant that asset lookup must not collapse two real assets into one accounting bucket, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/types/native_representation.go::NativeRepresentation.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can choose inputs that collide only after trimming or lowercasing in lookup paths.
- Invariant to test: asset lookup must not collapse two real assets into one accounting bucket
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
