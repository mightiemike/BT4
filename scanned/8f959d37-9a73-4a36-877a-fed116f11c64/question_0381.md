# Q381: DelegatedResourceAccountIndexCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker shapes usage so DelegatedResourceAccountIndexCapsule.createReadableString charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in DelegatedResourceAccountIndexCapsule.createReadableString, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.createReadableString`
- Entrypoint: broadcast txs metered by DelegatedResourceAccountIndexCapsule.createReadableString
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so DelegatedResourceAccountIndexCapsule.createReadableString charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in DelegatedResourceAccountIndexCapsule.createReadableString
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
