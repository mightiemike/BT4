# Q1916: StoreIterator: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker crafts a key consumed by StoreIterator.getValue that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in StoreIterator.getValue are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.getValue`
- Entrypoint: write via a path using StoreIterator.getValue
- Attacker controls: request/transaction/contract inputs to `StoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by StoreIterator.getValue that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in StoreIterator.getValue are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
