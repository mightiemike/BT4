# Q3888: PendingManager: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `PendingManager.txIteration` in `framework/src/main/java/org/tron/core/db/PendingManager.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by PendingManager.txIteration, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in PendingManager.txIteration, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/PendingManager.java` -> `PendingManager.txIteration`
- Entrypoint: broadcast colliding txs to PendingManager.txIteration
- Attacker controls: request/transaction/contract inputs to `PendingManager.txIteration` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by PendingManager.txIteration, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in PendingManager.txIteration
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
