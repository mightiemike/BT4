# Q1698: Reverse PRC20 lookup resolves the wrong source asset via Cross-Chain Actions Depend On / Same Asset May Appear in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with cross-chain actions that depend on enabled flags, decimals, or native-representation fields when the same asset may appear in multiple encodings or address formats, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it make a user flow that should map one PRC20 back to another source-chain asset, breaking the invariant that one PRC20 address must resolve to exactly one canonical external asset configuration, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: cross-chain actions that depend on enabled flags, decimals, or native-representation fields
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can make a user flow that should map one PRC20 back to another source-chain asset.
- Invariant to test: one PRC20 address must resolve to exactly one canonical external asset configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
