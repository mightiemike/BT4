# Q2809: RpcApiService: query pagination full-store scan

## Question
Can an unprivileged attacker (gRPC/HTTP Wallet API) abuse `RpcApiService.createTransactionCapsule` in `framework/src/main/java/org/tron/core/services/RpcApiService.java` — where the attacker calls the list/paginated variant behind RpcApiService.createTransactionCapsule with offset/limit forcing a full-store iteration — to break the invariant that paginated queries never scan the whole store for a bounded page, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/RpcApiService.java` -> `RpcApiService.createTransactionCapsule`
- Entrypoint: RpcApiService.createTransactionCapsule with large offset/limit
- Attacker controls: request/transaction/contract inputs to `RpcApiService.createTransactionCapsule` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls the list/paginated variant behind RpcApiService.createTransactionCapsule with offset/limit forcing a full-store iteration
- Invariant to test: paginated queries never scan the whole store for a bounded page
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: request a far page and measure iteration cost
