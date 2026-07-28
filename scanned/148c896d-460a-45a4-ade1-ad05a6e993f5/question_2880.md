# Q2880: Registry lookup treats semantically different assets as one via Chain Ids, Token Addresses, / Wrong Lookup Would Not in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when a wrong lookup would not be caught by offchain honesty alone, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it choose inputs that collide only after trimming or lowercasing in lookup paths, breaking the invariant that asset lookup must not collapse two real assets into one accounting bucket, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can choose inputs that collide only after trimming or lowercasing in lookup paths.
- Invariant to test: asset lookup must not collapse two real assets into one accounting bucket
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
