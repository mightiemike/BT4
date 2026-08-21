# Q3404: PendingManager: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `PendingManager.close` in `framework/src/main/java/org/tron/core/db/PendingManager.java` — where the attacker sets a permission_id in a transaction that PendingManager.close resolves out of range or to a default, bypassing the intended permission — to break the invariant that PendingManager.close rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/PendingManager.java` -> `PendingManager.close`
- Entrypoint: broadcast a tx with crafted permission_id via PendingManager.close
- Attacker controls: request/transaction/contract inputs to `PendingManager.close` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that PendingManager.close resolves out of range or to a default, bypassing the intended permission
- Invariant to test: PendingManager.close rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
