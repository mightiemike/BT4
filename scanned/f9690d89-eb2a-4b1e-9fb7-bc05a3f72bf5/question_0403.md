# Q403: TronStoreWithRevoking: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.getDbName` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker inflates the revoking/undo set through operations touching TronStoreWithRevoking.getDbName, growing memory per block — to break the invariant that undo state in TronStoreWithRevoking.getDbName is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.getDbName`
- Entrypoint: many state writes via TronStoreWithRevoking.getDbName
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.getDbName` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronStoreWithRevoking.getDbName, growing memory per block
- Invariant to test: undo state in TronStoreWithRevoking.getDbName is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
