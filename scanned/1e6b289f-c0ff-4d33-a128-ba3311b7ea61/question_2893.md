# Q2893: ChainBaseManager: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.hasBlocks` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker inflates the revoking/undo set through operations touching ChainBaseManager.hasBlocks, growing memory per block — to break the invariant that undo state in ChainBaseManager.hasBlocks is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.hasBlocks`
- Entrypoint: many state writes via ChainBaseManager.hasBlocks
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.hasBlocks` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching ChainBaseManager.hasBlocks, growing memory per block
- Invariant to test: undo state in ChainBaseManager.hasBlocks is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
