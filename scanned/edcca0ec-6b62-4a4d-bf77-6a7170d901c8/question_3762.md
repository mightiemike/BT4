# Q3762: TronDatabase: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.iterator` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker crafts a key consumed by TronDatabase.iterator that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in TronDatabase.iterator are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.iterator`
- Entrypoint: write via a path using TronDatabase.iterator
- Attacker controls: request/transaction/contract inputs to `TronDatabase.iterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by TronDatabase.iterator that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in TronDatabase.iterator are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
