# Q1995: TronDatabase: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.prefixQuery` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker inflates the revoking/undo set through operations touching TronDatabase.prefixQuery, growing memory per block — to break the invariant that undo state in TronDatabase.prefixQuery is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.prefixQuery`
- Entrypoint: many state writes via TronDatabase.prefixQuery
- Attacker controls: request/transaction/contract inputs to `TronDatabase.prefixQuery` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronDatabase.prefixQuery, growing memory per block
- Invariant to test: undo state in TronDatabase.prefixQuery is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
