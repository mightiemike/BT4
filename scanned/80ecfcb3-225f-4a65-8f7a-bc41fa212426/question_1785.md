# Q1785: TransactionCache: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCache.initCache` in `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` — where the attacker replays a transaction past its intended window because TransactionCache.initCache mis-checks expiration or ref-block — to break the invariant that TransactionCache.initCache rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` -> `TransactionCache.initCache`
- Entrypoint: rebroadcast a tx through TransactionCache.initCache
- Attacker controls: request/transaction/contract inputs to `TransactionCache.initCache` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionCache.initCache mis-checks expiration or ref-block
- Invariant to test: TransactionCache.initCache rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
