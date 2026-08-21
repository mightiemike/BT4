# Q201: ZenTransactionBuilder: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.addOutput` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker sends oversized/malformed bytes to ZenTransactionBuilder.addOutput that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that ZenTransactionBuilder.addOutput validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.addOutput`
- Entrypoint: shielded param to ZenTransactionBuilder.addOutput with bad length
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.addOutput` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to ZenTransactionBuilder.addOutput that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: ZenTransactionBuilder.addOutput validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
