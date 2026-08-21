# Q1923: TronStoreWithRevoking: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.iterator` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker inflates the revoking/undo set through operations touching TronStoreWithRevoking.iterator, growing memory per block — to break the invariant that undo state in TronStoreWithRevoking.iterator is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.iterator`
- Entrypoint: many state writes via TronStoreWithRevoking.iterator
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.iterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronStoreWithRevoking.iterator, growing memory per block
- Invariant to test: undo state in TronStoreWithRevoking.iterator is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
