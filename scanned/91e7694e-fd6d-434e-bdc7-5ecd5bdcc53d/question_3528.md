# Q3528: ChainBaseManager: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getWitnesses` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker inflates the revoking/undo set through operations touching ChainBaseManager.getWitnesses, growing memory per block — to break the invariant that undo state in ChainBaseManager.getWitnesses is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getWitnesses`
- Entrypoint: many state writes via ChainBaseManager.getWitnesses
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getWitnesses` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching ChainBaseManager.getWitnesses, growing memory per block
- Invariant to test: undo state in ChainBaseManager.getWitnesses is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
