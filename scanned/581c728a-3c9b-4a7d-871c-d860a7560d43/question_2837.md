# Q2837: DelegateResourceActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegateResourceActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java` — where the attacker orders operands in DelegateResourceActuator so DelegateResourceActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that DelegateResourceActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java` -> `DelegateResourceActuator.calcFee`
- Entrypoint: broadcast DelegateResourceActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `DelegateResourceActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in DelegateResourceActuator so DelegateResourceActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: DelegateResourceActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
