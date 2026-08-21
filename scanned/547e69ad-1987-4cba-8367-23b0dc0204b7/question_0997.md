# Q997: ChainBaseManager: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getBlockIdByNum` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker triggers ChainBaseManager.getBlockIdByNum paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in ChainBaseManager.getBlockIdByNum is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getBlockIdByNum`
- Entrypoint: repeated queries via ChainBaseManager.getBlockIdByNum
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getBlockIdByNum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers ChainBaseManager.getBlockIdByNum paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in ChainBaseManager.getBlockIdByNum is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress ChainBaseManager.getBlockIdByNum and watch handle/heap growth
