# Q857: NoteEncryption: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `NoteEncryption.attemptEncDecryption` in `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` — where the attacker replays a nullifier or stale anchor through NoteEncryption.attemptEncDecryption to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in NoteEncryption.attemptEncDecryption, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` -> `NoteEncryption.attemptEncDecryption`
- Entrypoint: shielded spend to NoteEncryption.attemptEncDecryption with reused nullifier
- Attacker controls: request/transaction/contract inputs to `NoteEncryption.attemptEncDecryption` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through NoteEncryption.attemptEncDecryption to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in NoteEncryption.attemptEncDecryption
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
