# Q3052: TronDatabase: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.put` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker inflates the revoking/undo set through operations touching TronDatabase.put, growing memory per block — to break the invariant that undo state in TronDatabase.put is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.put`
- Entrypoint: many state writes via TronDatabase.put
- Attacker controls: request/transaction/contract inputs to `TronDatabase.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronDatabase.put, growing memory per block
- Invariant to test: undo state in TronDatabase.put is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
