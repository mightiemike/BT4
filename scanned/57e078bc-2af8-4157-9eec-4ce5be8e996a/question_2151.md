# Q2151: ChainBaseManager: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getGenesisBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker inflates the revoking/undo set through operations touching ChainBaseManager.getGenesisBlockId, growing memory per block — to break the invariant that undo state in ChainBaseManager.getGenesisBlockId is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getGenesisBlockId`
- Entrypoint: many state writes via ChainBaseManager.getGenesisBlockId
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getGenesisBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching ChainBaseManager.getGenesisBlockId, growing memory per block
- Invariant to test: undo state in ChainBaseManager.getGenesisBlockId is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
