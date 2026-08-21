# Q3767: ReceiptCapsule: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ReceiptCapsule.setResult` in `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` — where the attacker submits a transaction whose ReceiptCapsule.setResult accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that ReceiptCapsule.setResult requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` -> `ReceiptCapsule.setResult`
- Entrypoint: broadcast a tx exercising ReceiptCapsule.setResult
- Attacker controls: request/transaction/contract inputs to `ReceiptCapsule.setResult` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose ReceiptCapsule.setResult accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: ReceiptCapsule.setResult requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
