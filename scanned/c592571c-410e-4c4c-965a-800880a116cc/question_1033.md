# Q1033: MaintenanceManager: maintenance timing edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MaintenanceManager.applyBlock` in `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java` — where the attacker times a transaction around the maintenance/reward cycle in MaintenanceManager.applyBlock to double-count or skip an update — to break the invariant that MaintenanceManager.applyBlock applies each cycle update exactly once, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java` -> `MaintenanceManager.applyBlock`
- Entrypoint: tx at maintenance boundary via MaintenanceManager.applyBlock
- Attacker controls: request/transaction/contract inputs to `MaintenanceManager.applyBlock` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times a transaction around the maintenance/reward cycle in MaintenanceManager.applyBlock to double-count or skip an update
- Invariant to test: MaintenanceManager.applyBlock applies each cycle update exactly once
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at cycle boundary asserting single update
