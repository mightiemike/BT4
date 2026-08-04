# Q3765: estimate-path cost bypass in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker use /wallet/deploycontract -> sign -> /wallet/broadcasttransaction so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr performs expensive contract execution, tracing, or validation on the estimate/read-only path with weaker cost controls than the state-changing path, causing Materially underpriced public execution work or public node degradation?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/deploycontract -> sign -> /wallet/broadcasttransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Compare fee limits, retry logic, tracing, and validation between estimate/read-only and full broadcast paths.
- Invariant to test: Estimate and read-only paths must not become a cheaper public doorway to essentially the same expensive work as full execution.
- Expected Immunefi impact: Materially underpriced public execution work or public node degradation
- Fast validation: Drive worst-case contracts through estimate and read-only APIs via /wallet/deploycontract -> sign -> /wallet/broadcasttransaction; compare resource use against the full broadcast path and charged limits.
