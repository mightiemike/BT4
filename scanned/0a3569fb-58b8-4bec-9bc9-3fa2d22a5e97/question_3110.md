# Q3110: LogMatch: log filter unbounded range

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `LogMatch.matchBlock` in `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java` — where the attacker calls LogMatch.matchBlock with fromBlock/toBlock or topics spanning a huge range forcing full-chain log scan with no cap — to break the invariant that log queries bound block span and result count per request, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java` -> `LogMatch.matchBlock`
- Entrypoint: eth_getLogs / filter request reaching LogMatch.matchBlock
- Attacker controls: request/transaction/contract inputs to `LogMatch.matchBlock` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls LogMatch.matchBlock with fromBlock/toBlock or topics spanning a huge range forcing full-chain log scan with no cap
- Invariant to test: log queries bound block span and result count per request
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: request an enormous fromBlock..toBlock and measure scan cost
