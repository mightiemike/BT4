# Q3254: JLibrustzcash: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashSaplingProvingCtxFree` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker sends oversized/malformed bytes to JLibrustzcash.librustzcashSaplingProvingCtxFree that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that JLibrustzcash.librustzcashSaplingProvingCtxFree validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashSaplingProvingCtxFree`
- Entrypoint: shielded param to JLibrustzcash.librustzcashSaplingProvingCtxFree with bad length
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashSaplingProvingCtxFree` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to JLibrustzcash.librustzcashSaplingProvingCtxFree that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: JLibrustzcash.librustzcashSaplingProvingCtxFree validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
