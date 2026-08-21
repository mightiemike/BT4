# Q3764: TransactionCapsule: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.setResult` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by TransactionCapsule.setResult, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in TransactionCapsule.setResult, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.setResult`
- Entrypoint: broadcast colliding txs to TransactionCapsule.setResult
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by TransactionCapsule.setResult, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in TransactionCapsule.setResult
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
