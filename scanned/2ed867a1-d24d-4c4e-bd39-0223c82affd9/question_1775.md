# Q1775: RockStoreIterator: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker crafts a key consumed by RockStoreIterator.getValue that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in RockStoreIterator.getValue are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.getValue`
- Entrypoint: write via a path using RockStoreIterator.getValue
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by RockStoreIterator.getValue that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in RockStoreIterator.getValue are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
