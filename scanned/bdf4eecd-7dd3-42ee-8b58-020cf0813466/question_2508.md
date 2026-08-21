# Q2508: StatisticManager: proposal parameter bound

## Question
Can an unprivileged attacker (broadcast transaction) abuse `StatisticManager.applyBlock` in `consensus/src/main/java/org/tron/consensus/dpos/StatisticManager.java` — where the attacker exploits a missing bound in StatisticManager.applyBlock so a user-reachable parameter path sets state out of range — to break the invariant that StatisticManager.applyBlock enforces min/max for every parameter it accepts, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/StatisticManager.java` -> `StatisticManager.applyBlock`
- Entrypoint: parameter path through StatisticManager.applyBlock
- Attacker controls: request/transaction/contract inputs to `StatisticManager.applyBlock` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits a missing bound in StatisticManager.applyBlock so a user-reachable parameter path sets state out of range
- Invariant to test: StatisticManager.applyBlock enforces min/max for every parameter it accepts
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit setting out-of-range value asserting rejection
