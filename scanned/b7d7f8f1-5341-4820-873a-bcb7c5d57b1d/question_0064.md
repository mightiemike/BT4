# Q64: AccountCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createDefaultOwnerPermission` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker replays a transaction past its intended window because AccountCapsule.createDefaultOwnerPermission mis-checks expiration or ref-block — to break the invariant that AccountCapsule.createDefaultOwnerPermission rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createDefaultOwnerPermission`
- Entrypoint: rebroadcast a tx through AccountCapsule.createDefaultOwnerPermission
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createDefaultOwnerPermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because AccountCapsule.createDefaultOwnerPermission mis-checks expiration or ref-block
- Invariant to test: AccountCapsule.createDefaultOwnerPermission rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
