# Q3492: TransactionCapsule: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.validateSignature` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker sets a permission_id in a transaction that TransactionCapsule.validateSignature resolves out of range or to a default, bypassing the intended permission — to break the invariant that TransactionCapsule.validateSignature rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.validateSignature`
- Entrypoint: broadcast a tx with crafted permission_id via TransactionCapsule.validateSignature
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.validateSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that TransactionCapsule.validateSignature resolves out of range or to a default, bypassing the intended permission
- Invariant to test: TransactionCapsule.validateSignature rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
