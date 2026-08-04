# Q3357: estimate-path cost bypass in RpcApiService.estimateEnergy

## Question
Can an unprivileged attacker use gRPC WalletApi / WalletSolidityApi so framework/src/main/java/org/tron/core/services/RpcApiService.java::estimateEnergy performs expensive contract execution, tracing, or validation on the estimate/read-only path with weaker cost controls than the state-changing path, causing Materially underpriced public execution work or public node degradation?

## Target
- File/function: framework/src/main/java/org/tron/core/services/RpcApiService.java::estimateEnergy
- Entrypoint: gRPC WalletApi / WalletSolidityApi
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Compare fee limits, retry logic, tracing, and validation between estimate/read-only and full broadcast paths.
- Invariant to test: Estimate and read-only paths must not become a cheaper public doorway to essentially the same expensive work as full execution.
- Expected Immunefi impact: Materially underpriced public execution work or public node degradation
- Fast validation: Drive worst-case contracts through estimate and read-only APIs via gRPC WalletApi / WalletSolidityApi; compare resource use against the full broadcast path and charged limits.
