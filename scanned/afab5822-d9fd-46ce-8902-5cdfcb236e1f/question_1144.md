# Q1144: CodeStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `CodeStore.getTotalCodes` in `chainbase/src/main/java/org/tron/core/store/CodeStore.java` — where the attacker crafts a key consumed by CodeStore.getTotalCodes that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in CodeStore.getTotalCodes are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/CodeStore.java` -> `CodeStore.getTotalCodes`
- Entrypoint: write via a path using CodeStore.getTotalCodes
- Attacker controls: request/transaction/contract inputs to `CodeStore.getTotalCodes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by CodeStore.getTotalCodes that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in CodeStore.getTotalCodes are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
