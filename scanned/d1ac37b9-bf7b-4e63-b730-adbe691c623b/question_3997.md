# Q3997: NoteEncryption: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `NoteEncryption.encryptToRecipient` in `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` — where the attacker submits a shielded proof or note to NoteEncryption.encryptToRecipient with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that NoteEncryption.encryptToRecipient binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` -> `NoteEncryption.encryptToRecipient`
- Entrypoint: shielded transaction reaching NoteEncryption.encryptToRecipient
- Attacker controls: request/transaction/contract inputs to `NoteEncryption.encryptToRecipient` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to NoteEncryption.encryptToRecipient with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: NoteEncryption.encryptToRecipient binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
