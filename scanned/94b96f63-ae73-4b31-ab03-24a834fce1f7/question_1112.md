# Q1112: TransactionCache: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCache.initCache` in `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` — where the attacker submits a transaction whose TransactionCache.initCache accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that TransactionCache.initCache requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` -> `TransactionCache.initCache`
- Entrypoint: broadcast a tx exercising TransactionCache.initCache
- Attacker controls: request/transaction/contract inputs to `TransactionCache.initCache` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose TransactionCache.initCache accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: TransactionCache.initCache requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
