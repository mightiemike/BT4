# Q3047: UpdateAccountActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAccountActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` — where the attacker orders operands in UpdateAccountActuator so UpdateAccountActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that UpdateAccountActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` -> `UpdateAccountActuator.calcFee`
- Entrypoint: broadcast UpdateAccountActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `UpdateAccountActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in UpdateAccountActuator so UpdateAccountActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: UpdateAccountActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
