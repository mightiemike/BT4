# Q419: ReceiptCapsule: permission parse abuse

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ReceiptCapsule.setResult` in `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` — where the attacker crafts a permission/contract field that ReceiptCapsule.setResult parses into an over-weight or malformed permission accepted downstream — to break the invariant that ReceiptCapsule.setResult bounds permission count, weight, and structure, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` -> `ReceiptCapsule.setResult`
- Entrypoint: broadcast a permission tx via ReceiptCapsule.setResult
- Attacker controls: request/transaction/contract inputs to `ReceiptCapsule.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a permission/contract field that ReceiptCapsule.setResult parses into an over-weight or malformed permission accepted downstream
- Invariant to test: ReceiptCapsule.setResult bounds permission count, weight, and structure
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with oversized permission asserting rejection
