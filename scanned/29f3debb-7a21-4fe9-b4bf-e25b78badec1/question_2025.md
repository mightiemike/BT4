# Q2025: LibrustzcashParam: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `LibrustzcashParam.validNull` in `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` — where the attacker sends oversized/malformed bytes to LibrustzcashParam.validNull that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that LibrustzcashParam.validNull validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` -> `LibrustzcashParam.validNull`
- Entrypoint: shielded param to LibrustzcashParam.validNull with bad length
- Attacker controls: request/transaction/contract inputs to `LibrustzcashParam.validNull` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to LibrustzcashParam.validNull that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: LibrustzcashParam.validNull validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
