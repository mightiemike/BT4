# Q1819: ChainBaseManager: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getBlockById` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker crafts a key consumed by ChainBaseManager.getBlockById that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in ChainBaseManager.getBlockById are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getBlockById`
- Entrypoint: write via a path using ChainBaseManager.getBlockById
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getBlockById` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by ChainBaseManager.getBlockById that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in ChainBaseManager.getBlockById are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
