# Q1425: AccountIdIndexStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `AccountIdIndexStore.has` in `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` — where the attacker crafts a key consumed by AccountIdIndexStore.has that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in AccountIdIndexStore.has are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` -> `AccountIdIndexStore.has`
- Entrypoint: write via a path using AccountIdIndexStore.has
- Attacker controls: request/transaction/contract inputs to `AccountIdIndexStore.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by AccountIdIndexStore.has that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in AccountIdIndexStore.has are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
