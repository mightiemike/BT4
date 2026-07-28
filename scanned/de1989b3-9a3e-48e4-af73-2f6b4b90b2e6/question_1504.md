# Q1504: Lookup failure falls back unsafely in a value-moving path via Address Caip-2 Formatting Variants / Wrong Lookup Would Not in BlockConfirmation.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when a wrong lookup would not be caught by offchain honesty alone, and cause `BlockConfirmation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it force a missing-registry edge case to continue with a default or stale interpretation, breaking the invariant that missing registry state must fail closed before any value movement occurs, and resulting in Permanent freezing of funds or wrong-asset transfer?

## Target
- File/function: x/uregistry/types/block_confirmation.go::BlockConfirmation.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `BlockConfirmation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can force a missing-registry edge case to continue with a default or stale interpretation.
- Invariant to test: missing registry state must fail closed before any value movement occurs
- Expected Immunefi impact: Permanent freezing of funds or wrong-asset transfer
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
