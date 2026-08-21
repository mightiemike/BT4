# Q809: AccountCapsule: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createDefaultActivePermission` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker sets a permission_id in a transaction that AccountCapsule.createDefaultActivePermission resolves out of range or to a default, bypassing the intended permission — to break the invariant that AccountCapsule.createDefaultActivePermission rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createDefaultActivePermission`
- Entrypoint: broadcast a tx with crafted permission_id via AccountCapsule.createDefaultActivePermission
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createDefaultActivePermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that AccountCapsule.createDefaultActivePermission resolves out of range or to a default, bypassing the intended permission
- Invariant to test: AccountCapsule.createDefaultActivePermission rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
