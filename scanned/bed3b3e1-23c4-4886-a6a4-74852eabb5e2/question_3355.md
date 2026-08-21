# Q3355: TronStoreWithRevoking: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.prefixQuery` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker inflates the revoking/undo set through operations touching TronStoreWithRevoking.prefixQuery, growing memory per block — to break the invariant that undo state in TronStoreWithRevoking.prefixQuery is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.prefixQuery`
- Entrypoint: many state writes via TronStoreWithRevoking.prefixQuery
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.prefixQuery` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching TronStoreWithRevoking.prefixQuery, growing memory per block
- Invariant to test: undo state in TronStoreWithRevoking.prefixQuery is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
