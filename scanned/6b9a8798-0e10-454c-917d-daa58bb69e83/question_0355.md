# Q355: RecentTransactionStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `RecentTransactionStore.<primary method>` in `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` — where the attacker inflates the revoking/undo set through operations touching RecentTransactionStore.<primary method>, growing memory per block — to break the invariant that undo state in RecentTransactionStore.<primary method> is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` -> `RecentTransactionStore.<primary method>`
- Entrypoint: many state writes via RecentTransactionStore.<primary method>
- Attacker controls: request/transaction/contract inputs to `RecentTransactionStore.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching RecentTransactionStore.<primary method>, growing memory per block
- Invariant to test: undo state in RecentTransactionStore.<primary method> is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
