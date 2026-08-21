# Q1935: CodeStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `CodeStore.getTotalCodes` in `chainbase/src/main/java/org/tron/core/store/CodeStore.java` — where the attacker inflates the revoking/undo set through operations touching CodeStore.getTotalCodes, growing memory per block — to break the invariant that undo state in CodeStore.getTotalCodes is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/CodeStore.java` -> `CodeStore.getTotalCodes`
- Entrypoint: many state writes via CodeStore.getTotalCodes
- Attacker controls: request/transaction/contract inputs to `CodeStore.getTotalCodes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching CodeStore.getTotalCodes, growing memory per block
- Invariant to test: undo state in CodeStore.getTotalCodes is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
