# Q1389: RockStoreIterator: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.hasNext` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker inflates the revoking/undo set through operations touching RockStoreIterator.hasNext, growing memory per block — to break the invariant that undo state in RockStoreIterator.hasNext is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.hasNext`
- Entrypoint: many state writes via RockStoreIterator.hasNext
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.hasNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching RockStoreIterator.hasNext, growing memory per block
- Invariant to test: undo state in RockStoreIterator.hasNext is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
