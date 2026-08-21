# Q1408: NoteEncryption: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `NoteEncryption.encryptToOurselves` in `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` — where the attacker forces NoteEncryption.encryptToOurselves to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in NoteEncryption.encryptToOurselves are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` -> `NoteEncryption.encryptToOurselves`
- Entrypoint: shielded input to NoteEncryption.encryptToOurselves maximizing tree work
- Attacker controls: request/transaction/contract inputs to `NoteEncryption.encryptToOurselves` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces NoteEncryption.encryptToOurselves to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in NoteEncryption.encryptToOurselves are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure NoteEncryption.encryptToOurselves work vs charged cost
