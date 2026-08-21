# Q1526: LibrustzcashParam: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `LibrustzcashParam.validObjectNull` in `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` — where the attacker sends oversized/malformed bytes to LibrustzcashParam.validObjectNull that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that LibrustzcashParam.validObjectNull validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` -> `LibrustzcashParam.validObjectNull`
- Entrypoint: shielded param to LibrustzcashParam.validObjectNull with bad length
- Attacker controls: request/transaction/contract inputs to `LibrustzcashParam.validObjectNull` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to LibrustzcashParam.validObjectNull that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: LibrustzcashParam.validObjectNull validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
