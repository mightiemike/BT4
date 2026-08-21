# Q3994: UpdateAssetActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAssetActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` — where the attacker orders operands in UpdateAssetActuator so UpdateAssetActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that UpdateAssetActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` -> `UpdateAssetActuator.validate`
- Entrypoint: broadcast UpdateAssetActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `UpdateAssetActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in UpdateAssetActuator so UpdateAssetActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: UpdateAssetActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
