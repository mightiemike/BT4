# Q1299: KeyIo: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `KeyIo.decodePaymentAddress` in `framework/src/main/java/org/tron/core/zen/address/KeyIo.java` — where the attacker submits a shielded proof or note to KeyIo.decodePaymentAddress with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that KeyIo.decodePaymentAddress binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/address/KeyIo.java` -> `KeyIo.decodePaymentAddress`
- Entrypoint: shielded transaction reaching KeyIo.decodePaymentAddress
- Attacker controls: request/transaction/contract inputs to `KeyIo.decodePaymentAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to KeyIo.decodePaymentAddress with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: KeyIo.decodePaymentAddress binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
