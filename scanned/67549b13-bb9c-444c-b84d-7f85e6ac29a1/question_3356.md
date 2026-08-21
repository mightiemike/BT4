# Q3356: PendingManager: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `PendingManager.txIteration` in `framework/src/main/java/org/tron/core/db/PendingManager.java` — where the attacker submits a transaction whose PendingManager.txIteration accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that PendingManager.txIteration requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/PendingManager.java` -> `PendingManager.txIteration`
- Entrypoint: broadcast a tx exercising PendingManager.txIteration
- Attacker controls: request/transaction/contract inputs to `PendingManager.txIteration` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose PendingManager.txIteration accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: PendingManager.txIteration requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
