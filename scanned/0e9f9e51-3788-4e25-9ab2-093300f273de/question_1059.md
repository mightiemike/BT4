# Q1059: ZenTransactionBuilder: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.addOutput` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker submits a shielded proof or note to ZenTransactionBuilder.addOutput with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that ZenTransactionBuilder.addOutput binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.addOutput`
- Entrypoint: shielded transaction reaching ZenTransactionBuilder.addOutput
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.addOutput` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to ZenTransactionBuilder.addOutput with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: ZenTransactionBuilder.addOutput binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
