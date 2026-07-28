# Q3274: Reverse PRC20 lookup resolves the wrong source asset via Address Caip-2 Formatting Variants / Live Flow Will Look in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when the live flow will look up chain and token config before moving value, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it make a user flow that should map one PRC20 back to another source-chain asset, breaking the invariant that one PRC20 address must resolve to exactly one canonical external asset configuration, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can make a user flow that should map one PRC20 back to another source-chain asset.
- Invariant to test: one PRC20 address must resolve to exactly one canonical external asset configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
