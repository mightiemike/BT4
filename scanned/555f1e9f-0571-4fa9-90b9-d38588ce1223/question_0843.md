# Q843: AccountAssetStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.getDeletedAssets` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker crafts a key consumed by AccountAssetStore.getDeletedAssets that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in AccountAssetStore.getDeletedAssets are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.getDeletedAssets`
- Entrypoint: write via a path using AccountAssetStore.getDeletedAssets
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.getDeletedAssets` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by AccountAssetStore.getDeletedAssets that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in AccountAssetStore.getDeletedAssets are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
