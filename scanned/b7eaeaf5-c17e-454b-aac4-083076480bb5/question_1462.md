# Q1462: AccountAssetStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.has` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker crafts a key consumed by AccountAssetStore.has that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in AccountAssetStore.has are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.has`
- Entrypoint: write via a path using AccountAssetStore.has
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by AccountAssetStore.has that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in AccountAssetStore.has are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
