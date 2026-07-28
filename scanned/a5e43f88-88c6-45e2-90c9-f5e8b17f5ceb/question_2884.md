# Q2884: Registry lookup treats semantically different assets as one via Chain Ids, Token Addresses, / Wrong Lookup Would Not in ChainConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when a wrong lookup would not be caught by offchain honesty alone, and cause `ChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it choose inputs that collide only after trimming or lowercasing in lookup paths, breaking the invariant that asset lookup must not collapse two real assets into one accounting bucket, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/types/chain_config.go::ChainConfig.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `ChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can choose inputs that collide only after trimming or lowercasing in lookup paths.
- Invariant to test: asset lookup must not collapse two real assets into one accounting bucket
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
