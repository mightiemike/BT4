# Q2325: RpcApiService: signature-weight recount

## Question
Can an unprivileged attacker (gRPC/HTTP Wallet API) abuse `RpcApiService.createTransactionCapsule` in `framework/src/main/java/org/tron/core/services/RpcApiService.java` — where the attacker submits a multi-sig transaction to RpcApiService.createTransactionCapsule whose weight is counted from raw signatures without canonicalization, double-counting a permission key — to break the invariant that permission weight counts each distinct key at most once after canonical recovery, leading to: Unauthorized account operations / asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/RpcApiService.java` -> `RpcApiService.createTransactionCapsule`
- Entrypoint: RpcApiService.createTransactionCapsule with a malleable duplicate signature set
- Attacker controls: request/transaction/contract inputs to `RpcApiService.createTransactionCapsule` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a multi-sig transaction to RpcApiService.createTransactionCapsule whose weight is counted from raw signatures without canonicalization, double-counting a permission key
- Invariant to test: permission weight counts each distinct key at most once after canonical recovery
- Expected Immunefi impact: Unauthorized account operations / asset theft (Critical)
- Fast validation: feed s and N-s signatures and assert weight not doubled
