# Q25: TransactionCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.setResult` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker replays a transaction past its intended window because TransactionCapsule.setResult mis-checks expiration or ref-block — to break the invariant that TransactionCapsule.setResult rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.setResult`
- Entrypoint: rebroadcast a tx through TransactionCapsule.setResult
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionCapsule.setResult mis-checks expiration or ref-block
- Invariant to test: TransactionCapsule.setResult rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
