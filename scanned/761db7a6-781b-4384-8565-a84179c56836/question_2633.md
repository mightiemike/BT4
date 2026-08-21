# Q2633: AccountCapsule: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker sets a permission_id in a transaction that AccountCapsule.createReadableString resolves out of range or to a default, bypassing the intended permission — to break the invariant that AccountCapsule.createReadableString rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createReadableString`
- Entrypoint: broadcast a tx with crafted permission_id via AccountCapsule.createReadableString
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that AccountCapsule.createReadableString resolves out of range or to a default, bypassing the intended permission
- Invariant to test: AccountCapsule.createReadableString rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
