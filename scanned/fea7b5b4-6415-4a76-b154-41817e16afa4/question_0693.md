# Q693: TransactionTrace: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionTrace.setResult` in `chainbase/src/main/java/org/tron/core/db/TransactionTrace.java` — where the attacker replays a transaction past its intended window because TransactionTrace.setResult mis-checks expiration or ref-block — to break the invariant that TransactionTrace.setResult rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TransactionTrace.java` -> `TransactionTrace.setResult`
- Entrypoint: rebroadcast a tx through TransactionTrace.setResult
- Attacker controls: request/transaction/contract inputs to `TransactionTrace.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionTrace.setResult mis-checks expiration or ref-block
- Invariant to test: TransactionTrace.setResult rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
