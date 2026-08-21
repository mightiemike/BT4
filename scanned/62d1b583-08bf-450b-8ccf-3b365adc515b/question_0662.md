# Q662: TronStoreWithRevoking: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.has` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker inflates the revoking/undo set through operations touching TronStoreWithRevoking.has, growing memory per block — to break the invariant that undo state in TronStoreWithRevoking.has is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.has`
- Entrypoint: many state writes via TronStoreWithRevoking.has
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronStoreWithRevoking.has, growing memory per block
- Invariant to test: undo state in TronStoreWithRevoking.has is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
