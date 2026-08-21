# Q3561: IncentiveManager: maintenance timing edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `IncentiveManager.reward` in `consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java` — where the attacker times a transaction around the maintenance/reward cycle in IncentiveManager.reward to double-count or skip an update — to break the invariant that IncentiveManager.reward applies each cycle update exactly once, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java` -> `IncentiveManager.reward`
- Entrypoint: tx at maintenance boundary via IncentiveManager.reward
- Attacker controls: request/transaction/contract inputs to `IncentiveManager.reward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times a transaction around the maintenance/reward cycle in IncentiveManager.reward to double-count or skip an update
- Invariant to test: IncentiveManager.reward applies each cycle update exactly once
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at cycle boundary asserting single update
