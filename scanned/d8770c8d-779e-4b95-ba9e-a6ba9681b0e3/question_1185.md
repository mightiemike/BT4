# Q1185: DBIterator: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `DBIterator.<primary method>` in `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` — where the attacker inflates the revoking/undo set through operations touching DBIterator.<primary method>, growing memory per block — to break the invariant that undo state in DBIterator.<primary method> is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` -> `DBIterator.<primary method>`
- Entrypoint: many state writes via DBIterator.<primary method>
- Attacker controls: request/transaction/contract inputs to `DBIterator.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching DBIterator.<primary method>, growing memory per block
- Invariant to test: undo state in DBIterator.<primary method> is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
