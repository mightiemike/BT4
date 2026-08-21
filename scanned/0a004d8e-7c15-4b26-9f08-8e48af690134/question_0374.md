# Q374: ChainBaseManager: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getHeadBlockNum` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker inflates the revoking/undo set through operations touching ChainBaseManager.getHeadBlockNum, growing memory per block — to break the invariant that undo state in ChainBaseManager.getHeadBlockNum is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getHeadBlockNum`
- Entrypoint: many state writes via ChainBaseManager.getHeadBlockNum
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getHeadBlockNum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching ChainBaseManager.getHeadBlockNum, growing memory per block
- Invariant to test: undo state in ChainBaseManager.getHeadBlockNum is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
