# Q0713: Decimals or native-representation mismatch corrupts amount semantics via Chain Ids, Token Addresses, / Same Asset May Appear in Keeper.GetTokenConfigByPRC20

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when the same asset may appear in multiple encodings or address formats, and cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so that it route a normal user deposit or refund through the wrong decimal or native-representation assumptions, breaking the invariant that registry amount semantics must preserve the same real asset quantity end to end, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/keeper.go::Keeper.GetTokenConfigByPRC20
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `Keeper.GetTokenConfigByPRC20` to return the wrong live object for attacker-controlled identifiers, so it can route a normal user deposit or refund through the wrong decimal or native-representation assumptions.
- Invariant to test: registry amount semantics must preserve the same real asset quantity end to end
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
