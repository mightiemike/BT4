# Q3327: FullNodeJsonRpcHttpService: log filter unbounded range

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `FullNodeJsonRpcHttpService.addFilter` in `framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java` — where the attacker calls FullNodeJsonRpcHttpService.addFilter with fromBlock/toBlock or topics spanning a huge range forcing full-chain log scan with no cap — to break the invariant that log queries bound block span and result count per request, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java` -> `FullNodeJsonRpcHttpService.addFilter`
- Entrypoint: eth_getLogs / filter request reaching FullNodeJsonRpcHttpService.addFilter
- Attacker controls: request/transaction/contract inputs to `FullNodeJsonRpcHttpService.addFilter` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls FullNodeJsonRpcHttpService.addFilter with fromBlock/toBlock or topics spanning a huge range forcing full-chain log scan with no cap
- Invariant to test: log queries bound block span and result count per request
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: request an enormous fromBlock..toBlock and measure scan cost
