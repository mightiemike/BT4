# Q1848: PendingManager: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `PendingManager.close` in `framework/src/main/java/org/tron/core/db/PendingManager.java` — where the attacker submits a transaction whose PendingManager.close accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that PendingManager.close requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/PendingManager.java` -> `PendingManager.close`
- Entrypoint: broadcast a tx exercising PendingManager.close
- Attacker controls: request/transaction/contract inputs to `PendingManager.close` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose PendingManager.close accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: PendingManager.close requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
