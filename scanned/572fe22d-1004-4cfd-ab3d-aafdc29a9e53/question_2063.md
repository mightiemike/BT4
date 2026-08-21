# Q2063: AccountCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createDbKey` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker replays a transaction past its intended window because AccountCapsule.createDbKey mis-checks expiration or ref-block — to break the invariant that AccountCapsule.createDbKey rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createDbKey`
- Entrypoint: rebroadcast a tx through AccountCapsule.createDbKey
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createDbKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because AccountCapsule.createDbKey mis-checks expiration or ref-block
- Invariant to test: AccountCapsule.createDbKey rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
