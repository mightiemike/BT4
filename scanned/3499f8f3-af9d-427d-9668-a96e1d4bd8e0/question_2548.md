# Q2548: LogFilter: log filter unbounded range

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `LogFilter.matchBloom` in `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java` — where the attacker calls LogFilter.matchBloom with fromBlock/toBlock or topics spanning a huge range forcing full-chain log scan with no cap — to break the invariant that log queries bound block span and result count per request, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java` -> `LogFilter.matchBloom`
- Entrypoint: eth_getLogs / filter request reaching LogFilter.matchBloom
- Attacker controls: request/transaction/contract inputs to `LogFilter.matchBloom` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls LogFilter.matchBloom with fromBlock/toBlock or topics spanning a huge range forcing full-chain log scan with no cap
- Invariant to test: log queries bound block span and result count per request
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: request an enormous fromBlock..toBlock and measure scan cost
