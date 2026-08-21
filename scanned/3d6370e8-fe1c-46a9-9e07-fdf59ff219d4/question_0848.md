# Q848: VotesCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VotesCapsule.getOldVotes` in `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` — where the attacker times VotesCapsule.getOldVotes to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that VotesCapsule.getOldVotes reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java` -> `VotesCapsule.getOldVotes`
- Entrypoint: broadcast metered by VotesCapsule.getOldVotes across a window boundary
- Attacker controls: request/transaction/contract inputs to `VotesCapsule.getOldVotes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times VotesCapsule.getOldVotes to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: VotesCapsule.getOldVotes reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
