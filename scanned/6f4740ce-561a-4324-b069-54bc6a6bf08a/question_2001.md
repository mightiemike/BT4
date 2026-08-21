# Q2001: TransactionCapsule: permission parse abuse

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.setResult` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker crafts a permission/contract field that TransactionCapsule.setResult parses into an over-weight or malformed permission accepted downstream — to break the invariant that TransactionCapsule.setResult bounds permission count, weight, and structure, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.setResult`
- Entrypoint: broadcast a permission tx via TransactionCapsule.setResult
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a permission/contract field that TransactionCapsule.setResult parses into an over-weight or malformed permission accepted downstream
- Invariant to test: TransactionCapsule.setResult bounds permission count, weight, and structure
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with oversized permission asserting rejection
