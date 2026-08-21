# Q943: VotesCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.addAllNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker shapes usage so VotesCapsule.addAllNewVotes charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in VotesCapsule.addAllNewVotes, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.addAllNewVotes`
- Entrypoint: broadcast txs metered by VotesCapsule.addAllNewVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.addAllNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so VotesCapsule.addAllNewVotes charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in VotesCapsule.addAllNewVotes
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
