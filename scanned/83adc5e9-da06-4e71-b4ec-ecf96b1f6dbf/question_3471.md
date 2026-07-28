# Q3471: Chain-id canonicalization resolves the wrong config via Chain Ids, Token Addresses, / Same Asset May Appear in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when the same asset may appear in multiple encodings or address formats, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it format a chain id so execution consumes another chain's enabled flags, gateway settings, or confirmations, breaking the invariant that each external chain id must bind to exactly one canonical config for execution, and resulting in Direct loss or permanent freeze of funds?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can format a chain id so execution consumes another chain's enabled flags, gateway settings, or confirmations.
- Invariant to test: each external chain id must bind to exactly one canonical config for execution
- Expected Immunefi impact: Direct loss or permanent freeze of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
