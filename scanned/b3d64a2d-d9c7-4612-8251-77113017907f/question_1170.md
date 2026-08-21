# Q1170: TransactionCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.validateSignature` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker replays a transaction past its intended window because TransactionCapsule.validateSignature mis-checks expiration or ref-block — to break the invariant that TransactionCapsule.validateSignature rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.validateSignature`
- Entrypoint: rebroadcast a tx through TransactionCapsule.validateSignature
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.validateSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionCapsule.validateSignature mis-checks expiration or ref-block
- Invariant to test: TransactionCapsule.validateSignature rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
