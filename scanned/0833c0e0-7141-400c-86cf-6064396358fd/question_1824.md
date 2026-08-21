# Q1824: ShieldedTRC20ParametersBuilder: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC` in `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` — where the attacker sends oversized/malformed bytes to ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` -> `ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC`
- Entrypoint: shielded param to ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC with bad length
- Attacker controls: request/transaction/contract inputs to `ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
