# Q1037: ZenTransactionBuilder: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `ZenTransactionBuilder.createSpendAuth` in `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` — where the attacker sends oversized/malformed bytes to ZenTransactionBuilder.createSpendAuth that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that ZenTransactionBuilder.createSpendAuth validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java` -> `ZenTransactionBuilder.createSpendAuth`
- Entrypoint: shielded param to ZenTransactionBuilder.createSpendAuth with bad length
- Attacker controls: request/transaction/contract inputs to `ZenTransactionBuilder.createSpendAuth` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to ZenTransactionBuilder.createSpendAuth that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: ZenTransactionBuilder.createSpendAuth validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
