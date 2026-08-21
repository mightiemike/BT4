# Q3284: LogFilter: filter map growth

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `LogFilter.initBlooms` in `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java` — where the attacker repeatedly registers filters via LogFilter.initBlooms without eviction, growing server memory unbounded — to break the invariant that per-client filter/state maps are bounded and evicted, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java` -> `LogFilter.initBlooms`
- Entrypoint: repeated newFilter calls at LogFilter.initBlooms
- Attacker controls: request/transaction/contract inputs to `LogFilter.initBlooms` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly registers filters via LogFilter.initBlooms without eviction, growing server memory unbounded
- Invariant to test: per-client filter/state maps are bounded and evicted
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: loop filter creation and watch heap
