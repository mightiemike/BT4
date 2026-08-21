# Q636: DelegatedResourceCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForEnergy` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker shapes usage so DelegatedResourceCapsule.addFrozenBalanceForEnergy charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in DelegatedResourceCapsule.addFrozenBalanceForEnergy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForEnergy`
- Entrypoint: broadcast txs metered by DelegatedResourceCapsule.addFrozenBalanceForEnergy
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForEnergy` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so DelegatedResourceCapsule.addFrozenBalanceForEnergy charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in DelegatedResourceCapsule.addFrozenBalanceForEnergy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
