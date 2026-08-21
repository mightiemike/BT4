# Q2023: ChainBaseManager: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getHeadBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker triggers ChainBaseManager.getHeadBlockId paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in ChainBaseManager.getHeadBlockId is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getHeadBlockId`
- Entrypoint: repeated queries via ChainBaseManager.getHeadBlockId
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getHeadBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers ChainBaseManager.getHeadBlockId paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in ChainBaseManager.getHeadBlockId is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress ChainBaseManager.getHeadBlockId and watch handle/heap growth
