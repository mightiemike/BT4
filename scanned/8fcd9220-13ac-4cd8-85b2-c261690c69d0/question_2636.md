# Q2636: ZenTransactionBuilder: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.addSpend` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker sends oversized/malformed bytes to ZenTransactionBuilder.addSpend that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that ZenTransactionBuilder.addSpend validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.addSpend`
- Entrypoint: shielded param to ZenTransactionBuilder.addSpend with bad length
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.addSpend` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to ZenTransactionBuilder.addSpend that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: ZenTransactionBuilder.addSpend validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
