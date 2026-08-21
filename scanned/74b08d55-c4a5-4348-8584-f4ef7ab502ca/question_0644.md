# Q644: PendingManager: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `PendingManager.close` in `framework/src/main/java/org/tron/core/db/PendingManager.java` — where the attacker replays a transaction past its intended window because PendingManager.close mis-checks expiration or ref-block — to break the invariant that PendingManager.close rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/PendingManager.java` -> `PendingManager.close`
- Entrypoint: rebroadcast a tx through PendingManager.close
- Attacker controls: request/transaction/contract inputs to `PendingManager.close` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because PendingManager.close mis-checks expiration or ref-block
- Invariant to test: PendingManager.close rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
