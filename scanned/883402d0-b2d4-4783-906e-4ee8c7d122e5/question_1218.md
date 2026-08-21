# Q1218: VotesCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getNewVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker times VotesCapsule.getNewVotes to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that VotesCapsule.getNewVotes reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getNewVotes`
- Entrypoint: broadcast metered by VotesCapsule.getNewVotes across a window boundary
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getNewVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times VotesCapsule.getNewVotes to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: VotesCapsule.getNewVotes reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
