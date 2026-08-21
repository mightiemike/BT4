# Q2855: TransactionCache: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCache.initCache` in `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` — where the attacker sets a permission_id in a transaction that TransactionCache.initCache resolves out of range or to a default, bypassing the intended permission — to break the invariant that TransactionCache.initCache rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` -> `TransactionCache.initCache`
- Entrypoint: broadcast a tx with crafted permission_id via TransactionCache.initCache
- Attacker controls: request/transaction/contract inputs to `TransactionCache.initCache` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that TransactionCache.initCache resolves out of range or to a default, bypassing the intended permission
- Invariant to test: TransactionCache.initCache rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
