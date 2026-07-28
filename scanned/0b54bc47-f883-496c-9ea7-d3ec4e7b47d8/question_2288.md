# Q2288: Decimals or native-representation mismatch corrupts amount semantics via Chain Ids, Token Addresses, / Registry-Derived Semantics Affect Mint, in isContractDeployed

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows when registry-derived semantics affect mint, refund, revert, or outbound routing, and cause `isContractDeployed` to trigger an unsafe state-transition edge case, so that it route a normal user deposit or refund through the wrong decimal or native-representation assumptions, breaking the invariant that registry amount semantics must preserve the same real asset quantity end to end, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/genesis.go::isContractDeployed
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: chain ids, token addresses, PRC20 addresses, recipients, and assets that are consumed by normal user bridge or payload flows
- Exploit idea: Cause `isContractDeployed` to trigger an unsafe state-transition edge case, so it can route a normal user deposit or refund through the wrong decimal or native-representation assumptions.
- Invariant to test: registry amount semantics must preserve the same real asset quantity end to end
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
