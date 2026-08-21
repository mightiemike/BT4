# Q1700: VotesCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.setOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker times VotesCapsule.setOldVotes to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that VotesCapsule.setOldVotes reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.setOldVotes`
- Entrypoint: broadcast metered by VotesCapsule.setOldVotes across a window boundary
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.setOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times VotesCapsule.setOldVotes to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: VotesCapsule.setOldVotes reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
