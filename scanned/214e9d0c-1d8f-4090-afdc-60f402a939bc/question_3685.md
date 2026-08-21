# Q3685: ChainBaseManager: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getGenesisBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker triggers ChainBaseManager.getGenesisBlockId paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in ChainBaseManager.getGenesisBlockId is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getGenesisBlockId`
- Entrypoint: repeated queries via ChainBaseManager.getGenesisBlockId
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getGenesisBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers ChainBaseManager.getGenesisBlockId paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in ChainBaseManager.getGenesisBlockId is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress ChainBaseManager.getGenesisBlockId and watch handle/heap growth
