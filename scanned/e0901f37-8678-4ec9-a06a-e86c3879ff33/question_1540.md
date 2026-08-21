# Q1540: StatisticManager: maintenance timing edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `StatisticManager.applyBlock` in `consensus/src/main/java/org/tron/consensus/dpos/StatisticManager.java` — where the attacker times a transaction around the maintenance/reward cycle in StatisticManager.applyBlock to double-count or skip an update — to break the invariant that StatisticManager.applyBlock applies each cycle update exactly once, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/StatisticManager.java` -> `StatisticManager.applyBlock`
- Entrypoint: tx at maintenance boundary via StatisticManager.applyBlock
- Attacker controls: request/transaction/contract inputs to `StatisticManager.applyBlock` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times a transaction around the maintenance/reward cycle in StatisticManager.applyBlock to double-count or skip an update
- Invariant to test: StatisticManager.applyBlock applies each cycle update exactly once
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at cycle boundary asserting single update
