# Q987: AccountAssetStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.getAssets` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker triggers AccountAssetStore.getAssets paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in AccountAssetStore.getAssets is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.getAssets`
- Entrypoint: repeated queries via AccountAssetStore.getAssets
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.getAssets` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers AccountAssetStore.getAssets paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in AccountAssetStore.getAssets is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress AccountAssetStore.getAssets and watch handle/heap growth
