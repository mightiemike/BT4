# Q892: DelegationStore: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.setWitnessVote` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker shapes usage so DelegationStore.setWitnessVote charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in DelegationStore.setWitnessVote, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.setWitnessVote`
- Entrypoint: broadcast txs metered by DelegationStore.setWitnessVote
- Attacker controls: request/transaction/contract inputs to `DelegationStore.setWitnessVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so DelegationStore.setWitnessVote charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in DelegationStore.setWitnessVote
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
