# Q2779: ZenTransactionBuilder: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.buildWithoutAsk` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker submits a shielded proof or note to ZenTransactionBuilder.buildWithoutAsk with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that ZenTransactionBuilder.buildWithoutAsk binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.buildWithoutAsk`
- Entrypoint: shielded transaction reaching ZenTransactionBuilder.buildWithoutAsk
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.buildWithoutAsk` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to ZenTransactionBuilder.buildWithoutAsk with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: ZenTransactionBuilder.buildWithoutAsk binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
