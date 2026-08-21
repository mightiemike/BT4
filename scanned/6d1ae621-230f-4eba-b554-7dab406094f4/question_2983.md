# Q2983: ReceiptCapsule: permission id out-of-range

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ReceiptCapsule.setResult` in `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` — where the attacker sets a permission_id in a transaction that ReceiptCapsule.setResult resolves out of range or to a default, bypassing the intended permission — to break the invariant that ReceiptCapsule.setResult rejects any permission_id not defined on the account, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` -> `ReceiptCapsule.setResult`
- Entrypoint: broadcast a tx with crafted permission_id via ReceiptCapsule.setResult
- Attacker controls: request/transaction/contract inputs to `ReceiptCapsule.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets a permission_id in a transaction that ReceiptCapsule.setResult resolves out of range or to a default, bypassing the intended permission
- Invariant to test: ReceiptCapsule.setResult rejects any permission_id not defined on the account
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with unknown permission_id asserting rejection
