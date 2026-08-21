# Q3678: AccountIdIndexStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `AccountIdIndexStore.getLowerCaseAccountId` in `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` — where the attacker calls a count/size path backed by AccountIdIndexStore.getLowerCaseAccountId that iterates the whole store per request — to break the invariant that AccountIdIndexStore.getLowerCaseAccountId answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` -> `AccountIdIndexStore.getLowerCaseAccountId`
- Entrypoint: query backed by AccountIdIndexStore.getLowerCaseAccountId
- Attacker controls: request/transaction/contract inputs to `AccountIdIndexStore.getLowerCaseAccountId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by AccountIdIndexStore.getLowerCaseAccountId that iterates the whole store per request
- Invariant to test: AccountIdIndexStore.getLowerCaseAccountId answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring AccountIdIndexStore.getLowerCaseAccountId cost vs store size
