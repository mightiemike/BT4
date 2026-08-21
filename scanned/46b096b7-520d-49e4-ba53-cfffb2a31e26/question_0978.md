# Q978: AccountAssetStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.getAssets` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker calls a count/size path backed by AccountAssetStore.getAssets that iterates the whole store per request — to break the invariant that AccountAssetStore.getAssets answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.getAssets`
- Entrypoint: query backed by AccountAssetStore.getAssets
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.getAssets` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by AccountAssetStore.getAssets that iterates the whole store per request
- Invariant to test: AccountAssetStore.getAssets answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring AccountAssetStore.getAssets cost vs store size
