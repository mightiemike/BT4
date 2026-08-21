# Q2775: JLibrustzcash: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashSaplingCheckOutput` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker sends oversized/malformed bytes to JLibrustzcash.librustzcashSaplingCheckOutput that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that JLibrustzcash.librustzcashSaplingCheckOutput validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashSaplingCheckOutput`
- Entrypoint: shielded param to JLibrustzcash.librustzcashSaplingCheckOutput with bad length
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashSaplingCheckOutput` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to JLibrustzcash.librustzcashSaplingCheckOutput that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: JLibrustzcash.librustzcashSaplingCheckOutput validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
