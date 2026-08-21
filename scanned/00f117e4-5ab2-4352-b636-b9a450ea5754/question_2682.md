# Q2682: AccountCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker replays a transaction past its intended window because AccountCapsule.createReadableString mis-checks expiration or ref-block — to break the invariant that AccountCapsule.createReadableString rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createReadableString`
- Entrypoint: rebroadcast a tx through AccountCapsule.createReadableString
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because AccountCapsule.createReadableString mis-checks expiration or ref-block
- Invariant to test: AccountCapsule.createReadableString rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
