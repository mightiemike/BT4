# Q471: RpcApiService: constant-call unmetered compute

## Question
Can an unprivileged attacker (gRPC/HTTP Wallet API) abuse `RpcApiService.callContract` in `framework/src/main/java/org/tron/core/services/RpcApiService.java` — where the attacker invokes RpcApiService.callContract (callConstantContract/estimateEnergy) with a contract that loops near the energy ceiling to burn node CPU for free — to break the invariant that constant/estimate execution is bounded and cannot exceed a strict energy/time cap, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/RpcApiService.java` -> `RpcApiService.callContract`
- Entrypoint: RpcApiService.callContract with a compute-heavy constant call
- Attacker controls: request/transaction/contract inputs to `RpcApiService.callContract` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: invokes RpcApiService.callContract (callConstantContract/estimateEnergy) with a contract that loops near the energy ceiling to burn node CPU for free
- Invariant to test: constant/estimate execution is bounded and cannot exceed a strict energy/time cap
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: submit a tight-loop constant call and measure CPU/time
