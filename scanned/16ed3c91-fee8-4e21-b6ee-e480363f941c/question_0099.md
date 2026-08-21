# Q99: TransactionContext: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionContext.<primary method>` in `chainbase/src/main/java/org/tron/core/db/TransactionContext.java` — where the attacker replays a transaction past its intended window because TransactionContext.<primary method> mis-checks expiration or ref-block — to break the invariant that TransactionContext.<primary method> rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TransactionContext.java` -> `TransactionContext.<primary method>`
- Entrypoint: rebroadcast a tx through TransactionContext.<primary method>
- Attacker controls: request/transaction/contract inputs to `TransactionContext.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionContext.<primary method> mis-checks expiration or ref-block
- Invariant to test: TransactionContext.<primary method> rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
