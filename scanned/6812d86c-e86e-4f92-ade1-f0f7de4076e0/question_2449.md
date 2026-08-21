# Q2449: TransactionCapsule: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.checkWeight` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker submits a transaction whose TransactionCapsule.checkWeight accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that TransactionCapsule.checkWeight requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.checkWeight`
- Entrypoint: broadcast a tx exercising TransactionCapsule.checkWeight
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.checkWeight` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose TransactionCapsule.checkWeight accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: TransactionCapsule.checkWeight requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
