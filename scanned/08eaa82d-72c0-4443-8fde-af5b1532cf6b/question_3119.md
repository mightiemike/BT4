# Q3119: IncentiveManager: proposal parameter bound

## Question
Can an unprivileged attacker (broadcast transaction) abuse `IncentiveManager.reward` in `consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java` — where the attacker exploits a missing bound in IncentiveManager.reward so a user-reachable parameter path sets state out of range — to break the invariant that IncentiveManager.reward enforces min/max for every parameter it accepts, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java` -> `IncentiveManager.reward`
- Entrypoint: parameter path through IncentiveManager.reward
- Attacker controls: request/transaction/contract inputs to `IncentiveManager.reward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits a missing bound in IncentiveManager.reward so a user-reachable parameter path sets state out of range
- Invariant to test: IncentiveManager.reward enforces min/max for every parameter it accepts
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit setting out-of-range value asserting rejection
