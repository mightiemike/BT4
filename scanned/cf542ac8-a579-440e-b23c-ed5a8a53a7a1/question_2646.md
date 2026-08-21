# Q2646: AccountStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.getSun` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker inflates the revoking/undo set through operations touching AccountStore.getSun, growing memory per block — to break the invariant that undo state in AccountStore.getSun is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.getSun`
- Entrypoint: many state writes via AccountStore.getSun
- Attacker controls: request/transaction/contract inputs to `AccountStore.getSun` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching AccountStore.getSun, growing memory per block
- Invariant to test: undo state in AccountStore.getSun is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
