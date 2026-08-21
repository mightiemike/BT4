# Q1372: DelegatedResourceStore: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceStore.unLockExpireResource` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` — where the attacker shapes usage so DelegatedResourceStore.unLockExpireResource charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in DelegatedResourceStore.unLockExpireResource, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java` -> `DelegatedResourceStore.unLockExpireResource`
- Entrypoint: broadcast txs metered by DelegatedResourceStore.unLockExpireResource
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceStore.unLockExpireResource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so DelegatedResourceStore.unLockExpireResource charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in DelegatedResourceStore.unLockExpireResource
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
