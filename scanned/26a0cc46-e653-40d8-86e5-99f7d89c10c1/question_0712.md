# Q0712: Decimals or native-representation mismatch corrupts amount semantics via Address Caip-2 Formatting Variants / Wrong Lookup Would Not in isContractDeployed

## Question
Can an unprivileged attacker enter through a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution with address and CAIP-2 formatting variants that target registry lookups when a wrong lookup would not be caught by offchain honesty alone, and cause `isContractDeployed` to trigger an unsafe state-transition edge case, so that it route a normal user deposit or refund through the wrong decimal or native-representation assumptions, breaking the invariant that registry amount semantics must preserve the same real asset quantity end to end, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/genesis.go::isContractDeployed
- Entrypoint: a normal inbound, payload, refund, or outbound flow that consumes `uregistry` mappings during execution
- Attacker controls: address and CAIP-2 formatting variants that target registry lookups
- Exploit idea: Cause `isContractDeployed` to trigger an unsafe state-transition edge case, so it can route a normal user deposit or refund through the wrong decimal or native-representation assumptions.
- Invariant to test: registry amount semantics must preserve the same real asset quantity end to end
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper or integration test that drives a normal user flow through the crafted registry lookup and inspect the chosen chain/token semantics
