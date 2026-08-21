# Q622: AccountCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createDefaultActivePermission` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker replays a transaction past its intended window because AccountCapsule.createDefaultActivePermission mis-checks expiration or ref-block — to break the invariant that AccountCapsule.createDefaultActivePermission rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createDefaultActivePermission`
- Entrypoint: rebroadcast a tx through AccountCapsule.createDefaultActivePermission
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createDefaultActivePermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because AccountCapsule.createDefaultActivePermission mis-checks expiration or ref-block
- Invariant to test: AccountCapsule.createDefaultActivePermission rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
