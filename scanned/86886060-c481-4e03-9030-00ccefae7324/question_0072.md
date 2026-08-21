# Q72: MaintenanceManager: fork-gate version mismatch

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MaintenanceManager.applyBlock` in `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java` — where the attacker submits a transaction valid under one fork-gate reading of MaintenanceManager.applyBlock but invalid under another, splitting nodes — to break the invariant that MaintenanceManager.applyBlock evaluates the fork condition identically on every node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java` -> `MaintenanceManager.applyBlock`
- Entrypoint: broadcast a tx near a fork boundary via MaintenanceManager.applyBlock
- Attacker controls: request/transaction/contract inputs to `MaintenanceManager.applyBlock` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction valid under one fork-gate reading of MaintenanceManager.applyBlock but invalid under another, splitting nodes
- Invariant to test: MaintenanceManager.applyBlock evaluates the fork condition identically on every node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential test with gate on/off asserting same verdict
