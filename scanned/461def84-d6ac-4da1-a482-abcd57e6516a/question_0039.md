# Q39: KeyIo: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `KeyIo.encodePaymentAddress` in `framework/src/main/java/org/tron/core/zen/address/KeyIo.java` — where the attacker sends oversized/malformed bytes to KeyIo.encodePaymentAddress that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that KeyIo.encodePaymentAddress validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/address/KeyIo.java` -> `KeyIo.encodePaymentAddress`
- Entrypoint: shielded param to KeyIo.encodePaymentAddress with bad length
- Attacker controls: request/transaction/contract inputs to `KeyIo.encodePaymentAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to KeyIo.encodePaymentAddress that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: KeyIo.encodePaymentAddress validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
