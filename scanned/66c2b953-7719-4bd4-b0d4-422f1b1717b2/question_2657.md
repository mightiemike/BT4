# Q2657: ChainBaseManager: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getSolidBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker crafts a key consumed by ChainBaseManager.getSolidBlockId that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in ChainBaseManager.getSolidBlockId are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getSolidBlockId`
- Entrypoint: write via a path using ChainBaseManager.getSolidBlockId
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getSolidBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by ChainBaseManager.getSolidBlockId that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in ChainBaseManager.getSolidBlockId are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
