# Q1490: Manager: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToMoveAbi` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker sets a permission_id in a transaction that Manager.needToMoveAbi resolves out of range or to a default, bypassing the intended permission — to break the invariant that Manager.needToMoveAbi rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToMoveAbi`
- Entrypoint: broadcast a tx with crafted permission_id via Manager.needToMoveAbi
- Attacker controls: request/transaction/contract inputs to `Manager.needToMoveAbi` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that Manager.needToMoveAbi resolves out of range or to a default, bypassing the intended permission
- Invariant to test: Manager.needToMoveAbi rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
