# Q1197: VotesCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.addOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker times VotesCapsule.addOldVotes to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that VotesCapsule.addOldVotes reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.addOldVotes`
- Entrypoint: broadcast metered by VotesCapsule.addOldVotes across a window boundary
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.addOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times VotesCapsule.addOldVotes to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: VotesCapsule.addOldVotes reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
