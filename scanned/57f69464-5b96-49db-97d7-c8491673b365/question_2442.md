# Q2442: VotesCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker shapes usage so VotesCapsule.getOldVotes charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in VotesCapsule.getOldVotes, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getOldVotes`
- Entrypoint: broadcast txs metered by VotesCapsule.getOldVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so VotesCapsule.getOldVotes charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in VotesCapsule.getOldVotes
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
