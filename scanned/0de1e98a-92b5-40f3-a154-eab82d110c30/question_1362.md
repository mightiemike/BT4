# Q1362: NoteEncryption: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `NoteEncryption.prfOck` in `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` — where the attacker sends oversized/malformed bytes to NoteEncryption.prfOck that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that NoteEncryption.prfOck validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` -> `NoteEncryption.prfOck`
- Entrypoint: shielded param to NoteEncryption.prfOck with bad length
- Attacker controls: request/transaction/contract inputs to `NoteEncryption.prfOck` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to NoteEncryption.prfOck that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: NoteEncryption.prfOck validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
