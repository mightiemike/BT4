# Q2206: MaintenanceManager: proposal parameter bound

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MaintenanceManager.applyBlock` in `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java` — where the attacker exploits a missing bound in MaintenanceManager.applyBlock so a user-reachable parameter path sets state out of range — to break the invariant that MaintenanceManager.applyBlock enforces min/max for every parameter it accepts, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java` -> `MaintenanceManager.applyBlock`
- Entrypoint: parameter path through MaintenanceManager.applyBlock
- Attacker controls: request/transaction/contract inputs to `MaintenanceManager.applyBlock` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits a missing bound in MaintenanceManager.applyBlock so a user-reachable parameter path sets state out of range
- Invariant to test: MaintenanceManager.applyBlock enforces min/max for every parameter it accepts
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit setting out-of-range value asserting rejection
