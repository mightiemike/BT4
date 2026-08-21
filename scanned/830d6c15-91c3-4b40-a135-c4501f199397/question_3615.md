# Q3615: AccountCapsule: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.compareTo` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker replays a transaction past its intended window because AccountCapsule.compareTo mis-checks expiration or ref-block — to break the invariant that AccountCapsule.compareTo rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.compareTo`
- Entrypoint: rebroadcast a tx through AccountCapsule.compareTo
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.compareTo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because AccountCapsule.compareTo mis-checks expiration or ref-block
- Invariant to test: AccountCapsule.compareTo rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
