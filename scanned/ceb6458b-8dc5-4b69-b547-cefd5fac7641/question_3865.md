# Q3865: Decimals or native-representation mismatch corrupts amount semantics via Cross-Chain Actions Depend On / Wrong Lookup Would Not in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with cross-chain actions that depend on enabled flags, decimals, or native-representation fields when a wrong lookup would not be caught by offchain honesty alone, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it route a normal user deposit or refund through the wrong decimal or native-representation assumptions, breaking the invariant that registry amount semantics must preserve the same real asset quantity end to end, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: cross-chain actions that depend on enabled flags, decimals, or native-representation fields
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can route a normal user deposit or refund through the wrong decimal or native-representation assumptions.
- Invariant to test: registry amount semantics must preserve the same real asset quantity end to end
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
