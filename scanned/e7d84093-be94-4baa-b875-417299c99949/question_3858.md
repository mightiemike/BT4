# Q3858: NoteEncryption: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `NoteEncryption.attemptEncDecryption` in `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` — where the attacker sends oversized/malformed bytes to NoteEncryption.attemptEncDecryption that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that NoteEncryption.attemptEncDecryption validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` -> `NoteEncryption.attemptEncDecryption`
- Entrypoint: shielded param to NoteEncryption.attemptEncDecryption with bad length
- Attacker controls: request/transaction/contract inputs to `NoteEncryption.attemptEncDecryption` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to NoteEncryption.attemptEncDecryption that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: NoteEncryption.attemptEncDecryption validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
