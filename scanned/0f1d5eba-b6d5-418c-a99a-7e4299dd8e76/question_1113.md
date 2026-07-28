# Q1113: Gateway or vault method selection misroutes user value via Chain Ids, Token Addresses, / Wrong Lookup Would Not in GatewayMethods.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when a wrong lookup would not be caught by offchain honesty alone, and cause `GatewayMethods.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it make registry-derived method metadata point a live flow at the wrong destination semantics, breaking the invariant that registry-selected methods must remain bound to the intended chain and asset only, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/types/gateway_methods.go::GatewayMethods.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `GatewayMethods.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can make registry-derived method metadata point a live flow at the wrong destination semantics.
- Invariant to test: registry-selected methods must remain bound to the intended chain and asset only
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
