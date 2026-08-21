# Q188: AccountIdIndexStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `AccountIdIndexStore.getLowerCaseAccountId` in `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` — where the attacker inflates the revoking/undo set through operations touching AccountIdIndexStore.getLowerCaseAccountId, growing memory per block — to break the invariant that undo state in AccountIdIndexStore.getLowerCaseAccountId is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` -> `AccountIdIndexStore.getLowerCaseAccountId`
- Entrypoint: many state writes via AccountIdIndexStore.getLowerCaseAccountId
- Attacker controls: request/transaction/contract inputs to `AccountIdIndexStore.getLowerCaseAccountId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching AccountIdIndexStore.getLowerCaseAccountId, growing memory per block
- Invariant to test: undo state in AccountIdIndexStore.getLowerCaseAccountId is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
