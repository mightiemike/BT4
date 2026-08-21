# Q1254: AccountAssetStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.getDeletedAssets` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker seeds keys so a query iterating AccountAssetStore.getDeletedAssets performs an unbounded prefix scan on each request — to break the invariant that iteration in AccountAssetStore.getDeletedAssets is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.getDeletedAssets`
- Entrypoint: query backed by AccountAssetStore.getDeletedAssets after seeding keys
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.getDeletedAssets` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating AccountAssetStore.getDeletedAssets performs an unbounded prefix scan on each request
- Invariant to test: iteration in AccountAssetStore.getDeletedAssets is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring AccountAssetStore.getDeletedAssets scan growth
