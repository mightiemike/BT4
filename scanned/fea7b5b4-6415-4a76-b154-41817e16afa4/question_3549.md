# Q3549: TronDatabase: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.iterator` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker inflates the revoking/undo set through operations touching TronDatabase.iterator, growing memory per block — to break the invariant that undo state in TronDatabase.iterator is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.iterator`
- Entrypoint: many state writes via TronDatabase.iterator
- Attacker controls: request/transaction/contract inputs to `TronDatabase.iterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronDatabase.iterator, growing memory per block
- Invariant to test: undo state in TronDatabase.iterator is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
