# Q3506: FullNodeJsonRpcHttpService: estimateGas/call resource abuse

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `FullNodeJsonRpcHttpService.addServlet` in `framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java` — where the attacker drives FullNodeJsonRpcHttpService.addServlet into an expensive constant-call or estimate path that runs heavy EVM work without charging energy — to break the invariant that off-chain call/estimate paths are bounded in CPU and time, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java` -> `FullNodeJsonRpcHttpService.addServlet`
- Entrypoint: eth_call/eth_estimateGas to FullNodeJsonRpcHttpService.addServlet with a heavy payload
- Attacker controls: request/transaction/contract inputs to `FullNodeJsonRpcHttpService.addServlet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives FullNodeJsonRpcHttpService.addServlet into an expensive constant-call or estimate path that runs heavy EVM work without charging energy
- Invariant to test: off-chain call/estimate paths are bounded in CPU and time
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: submit a gas-heavy constant call and measure server CPU
