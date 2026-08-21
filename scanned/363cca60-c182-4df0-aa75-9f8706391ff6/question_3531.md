# Q3531: UpdateAccountActuator: frozen/delegated double-spend

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAccountActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` — where the attacker uses UpdateAccountActuator to freeze, delegate, or unfreeze the same balance twice across V1/V2 paths so resource is granted twice — to break the invariant that a unit of stake backs at most one active resource grant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` -> `UpdateAccountActuator.execute`
- Entrypoint: interleave UpdateAccountActuator with the paired freeze/unfreeze actuator
- Attacker controls: request/transaction/contract inputs to `UpdateAccountActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses UpdateAccountActuator to freeze, delegate, or unfreeze the same balance twice across V1/V2 paths so resource is granted twice
- Invariant to test: a unit of stake backs at most one active resource grant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit sequence freezing then delegating same balance in both paths
