# Q3766: cache-lifecycle confusion in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker use /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction to create stale filters, stale cache entries, or stale response objects that framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr later reuses across unrelated requests, enabling Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Abuse filter creation/removal timing, repeated ids, old block ranges, and API restarts or close paths that may keep stale objects alive.
- Invariant to test: Cache and filter lifecycle state must stay request-scoped and must never cross-contaminate later requests or keep unbounded work alive.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Create and retire large numbers of public filters or cached objects through /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction; assert they cannot be resurrected, leaked, or reused cross-request.
