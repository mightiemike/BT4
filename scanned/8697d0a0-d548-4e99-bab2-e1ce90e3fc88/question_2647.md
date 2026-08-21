# Q2647: NoteEncryption: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `NoteEncryption.kdfSapling` in `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` — where the attacker forces NoteEncryption.kdfSapling to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in NoteEncryption.kdfSapling are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java` -> `NoteEncryption.kdfSapling`
- Entrypoint: shielded input to NoteEncryption.kdfSapling maximizing tree work
- Attacker controls: request/transaction/contract inputs to `NoteEncryption.kdfSapling` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces NoteEncryption.kdfSapling to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in NoteEncryption.kdfSapling are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure NoteEncryption.kdfSapling work vs charged cost
