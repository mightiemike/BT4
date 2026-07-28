# Q1310: Registry lookup treats semantically different assets as one via User-Controlled Inbound Outbound Forces / Live Flow Will Look in GatewayMethods.ValidateBasic

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with a user-controlled inbound or outbound that forces reverse lookup from PRC20 back to source asset when the live flow will look up chain and token config before moving value, and cause `GatewayMethods.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it choose inputs that collide only after trimming or lowercasing in lookup paths, breaking the invariant that asset lookup must not collapse two real assets into one accounting bucket, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uregistry/types/gateway_methods.go::GatewayMethods.ValidateBasic
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: a user-controlled inbound or outbound that forces reverse lookup from PRC20 back to source asset
- Exploit idea: Cause `GatewayMethods.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can choose inputs that collide only after trimming or lowercasing in lookup paths.
- Invariant to test: asset lookup must not collapse two real assets into one accounting bucket
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
