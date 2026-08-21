# Q3446: AccountAssetStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.getAssets` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker inflates the revoking/undo set through operations touching AccountAssetStore.getAssets, growing memory per block — to break the invariant that undo state in AccountAssetStore.getAssets is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.getAssets`
- Entrypoint: many state writes via AccountAssetStore.getAssets
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.getAssets` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching AccountAssetStore.getAssets, growing memory per block
- Invariant to test: undo state in AccountAssetStore.getAssets is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
