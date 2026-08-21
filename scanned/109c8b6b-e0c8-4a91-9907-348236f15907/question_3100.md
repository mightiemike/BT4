# Q3100: AccountStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.getZion` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker inflates the revoking/undo set through operations touching AccountStore.getZion, growing memory per block — to break the invariant that undo state in AccountStore.getZion is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.getZion`
- Entrypoint: many state writes via AccountStore.getZion
- Attacker controls: request/transaction/contract inputs to `AccountStore.getZion` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching AccountStore.getZion, growing memory per block
- Invariant to test: undo state in AccountStore.getZion is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
