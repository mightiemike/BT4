# Q3814: StoreIterator: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.hasNext` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker inflates the revoking/undo set through operations touching StoreIterator.hasNext, growing memory per block — to break the invariant that undo state in StoreIterator.hasNext is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.hasNext`
- Entrypoint: many state writes via StoreIterator.hasNext
- Attacker controls: request/transaction/contract inputs to `StoreIterator.hasNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching StoreIterator.hasNext, growing memory per block
- Invariant to test: undo state in StoreIterator.hasNext is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
