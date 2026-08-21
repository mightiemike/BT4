# Q802: ChainBaseManager: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getHeadBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker seeds keys so a query iterating ChainBaseManager.getHeadBlockId performs an unbounded prefix scan on each request — to break the invariant that iteration in ChainBaseManager.getHeadBlockId is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getHeadBlockId`
- Entrypoint: query backed by ChainBaseManager.getHeadBlockId after seeding keys
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getHeadBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating ChainBaseManager.getHeadBlockId performs an unbounded prefix scan on each request
- Invariant to test: iteration in ChainBaseManager.getHeadBlockId is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring ChainBaseManager.getHeadBlockId scan growth
