# Q2100: Token-address canonicalization points to the wrong whitelist entry via Chain Ids, Token Addresses, / Wrong Lookup Would Not in NativeRepresentation.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when a wrong lookup would not be caught by offchain honesty alone, and cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it use address-formatting variants to make one token flow use another token's config, breaking the invariant that one external token should map to exactly one canonical config regardless of formatting, and resulting in Direct theft/loss or wrong-party minting?

## Target
- File/function: x/uregistry/types/native_representation.go::NativeRepresentation.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `NativeRepresentation.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can use address-formatting variants to make one token flow use another token's config.
- Invariant to test: one external token should map to exactly one canonical config regardless of formatting
- Expected Immunefi impact: Direct theft/loss or wrong-party minting
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
