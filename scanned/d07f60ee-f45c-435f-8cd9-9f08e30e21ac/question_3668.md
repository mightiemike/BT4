# Q3668: Token-address canonicalization points to the wrong whitelist entry via User-Controlled Inbound Outbound Forces / Registry-Derived Semantics Affect Mint, in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with a user-controlled inbound or outbound that forces reverse lookup from PRC20 back to source asset when registry-derived semantics affect mint, refund, revert, or outbound routing, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it use address-formatting variants to make one token flow use another token's config, breaking the invariant that one external token should map to exactly one canonical config regardless of formatting, and resulting in Direct theft/loss or wrong-party minting?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: a user-controlled inbound or outbound that forces reverse lookup from PRC20 back to source asset
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can use address-formatting variants to make one token flow use another token's config.
- Invariant to test: one external token should map to exactly one canonical config regardless of formatting
- Expected Immunefi impact: Direct theft/loss or wrong-party minting
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
