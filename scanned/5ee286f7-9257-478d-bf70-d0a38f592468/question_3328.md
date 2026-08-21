# Q3328: TronStoreWithRevoking: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.getUnchecked` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker inflates the revoking/undo set through operations touching TronStoreWithRevoking.getUnchecked, growing memory per block — to break the invariant that undo state in TronStoreWithRevoking.getUnchecked is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.getUnchecked`
- Entrypoint: many state writes via TronStoreWithRevoking.getUnchecked
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.getUnchecked` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronStoreWithRevoking.getUnchecked, growing memory per block
- Invariant to test: undo state in TronStoreWithRevoking.getUnchecked is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
