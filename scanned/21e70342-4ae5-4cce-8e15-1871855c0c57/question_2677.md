# Q2677: DelegatedResourceAccountIndexCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker shapes usage so DelegatedResourceAccountIndexCapsule.addToAccount charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in DelegatedResourceAccountIndexCapsule.addToAccount, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addToAccount`
- Entrypoint: broadcast txs metered by DelegatedResourceAccountIndexCapsule.addToAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so DelegatedResourceAccountIndexCapsule.addToAccount charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in DelegatedResourceAccountIndexCapsule.addToAccount
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
