# Q3432: AccountAssetStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `AccountAssetStore.has` in `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` — where the attacker calls a count/size path backed by AccountAssetStore.has that iterates the whole store per request — to break the invariant that AccountAssetStore.has answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java` -> `AccountAssetStore.has`
- Entrypoint: query backed by AccountAssetStore.has
- Attacker controls: request/transaction/contract inputs to `AccountAssetStore.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by AccountAssetStore.has that iterates the whole store per request
- Invariant to test: AccountAssetStore.has answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring AccountAssetStore.has cost vs store size
