# Q3777: AccountAssetStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.getAssets` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker seeds keys so a query iterating AccountAssetStore.getAssets performs an unbounded prefix scan on each request — to break the invariant that iteration in AccountAssetStore.getAssets is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.getAssets`
- Entrypoint: query backed by AccountAssetStore.getAssets after seeding keys
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.getAssets` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating AccountAssetStore.getAssets performs an unbounded prefix scan on each request
- Invariant to test: iteration in AccountAssetStore.getAssets is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring AccountAssetStore.getAssets scan growth
