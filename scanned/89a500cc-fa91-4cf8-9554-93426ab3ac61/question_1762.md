# Q1762: VotesCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.setOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker shapes usage so VotesCapsule.setOldVotes charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in VotesCapsule.setOldVotes, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.setOldVotes`
- Entrypoint: broadcast txs metered by VotesCapsule.setOldVotes
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.setOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so VotesCapsule.setOldVotes charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in VotesCapsule.setOldVotes
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
