# Q3077: Lookup failure falls back unsafely in a value-moving path via User-Controlled Inbound Outbound Forces / Live Flow Will Look in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with a user-controlled inbound or outbound that forces reverse lookup from PRC20 back to source asset when the live flow will look up chain and token config before moving value, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it force a missing-registry edge case to continue with a default or stale interpretation, breaking the invariant that missing registry state must fail closed before any value movement occurs, and resulting in Permanent freezing of funds or wrong-asset transfer?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: a user-controlled inbound or outbound that forces reverse lookup from PRC20 back to source asset
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can force a missing-registry edge case to continue with a default or stale interpretation.
- Invariant to test: missing registry state must fail closed before any value movement occurs
- Expected Immunefi impact: Permanent freezing of funds or wrong-asset transfer
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
