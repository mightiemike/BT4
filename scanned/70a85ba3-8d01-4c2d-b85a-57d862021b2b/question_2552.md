# Q2552: StoreIterator: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker inflates the revoking/undo set through operations touching StoreIterator.getValue, growing memory per block — to break the invariant that undo state in StoreIterator.getValue is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.getValue`
- Entrypoint: many state writes via StoreIterator.getValue
- Attacker controls: request/transaction/contract inputs to `StoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching StoreIterator.getValue, growing memory per block
- Invariant to test: undo state in StoreIterator.getValue is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
