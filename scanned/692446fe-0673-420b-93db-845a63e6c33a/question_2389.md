# Q2389: PendingManager: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `PendingManager.txIteration` in `framework/src/main/java/org/tron/core/db/PendingManager.java` — where the attacker replays a transaction past its intended window because PendingManager.txIteration mis-checks expiration or ref-block — to break the invariant that PendingManager.txIteration rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/PendingManager.java` -> `PendingManager.txIteration`
- Entrypoint: rebroadcast a tx through PendingManager.txIteration
- Attacker controls: request/transaction/contract inputs to `PendingManager.txIteration` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because PendingManager.txIteration mis-checks expiration or ref-block
- Invariant to test: PendingManager.txIteration rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
