# Q3874: cache-lifecycle confusion in QpsStrategy.defaultParam

## Question
Can an unprivileged attacker use /wallet/* public HTTP APIs to create stale filters, stale cache entries, or stale response objects that framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java::defaultParam later reuses across unrelated requests, enabling Persistent stuck filters, pending objects, or API-driven frozen state?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java::defaultParam
- Entrypoint: /wallet/* public HTTP APIs
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Abuse filter creation/removal timing, repeated ids, old block ranges, and API restarts or close paths that may keep stale objects alive.
- Invariant to test: Cache and filter lifecycle state must stay request-scoped and must never cross-contaminate later requests or keep unbounded work alive.
- Expected Immunefi impact: Persistent stuck filters, pending objects, or API-driven frozen state
- Fast validation: Create and retire large numbers of public filters or cached objects through /wallet/* public HTTP APIs; assert they cannot be resurrected, leaked, or reused cross-request.
