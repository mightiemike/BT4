# Q3723: JLibrustzcash: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashToScalar` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker sends oversized/malformed bytes to JLibrustzcash.librustzcashToScalar that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that JLibrustzcash.librustzcashToScalar validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashToScalar`
- Entrypoint: shielded param to JLibrustzcash.librustzcashToScalar with bad length
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashToScalar` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to JLibrustzcash.librustzcashToScalar that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: JLibrustzcash.librustzcashToScalar validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
