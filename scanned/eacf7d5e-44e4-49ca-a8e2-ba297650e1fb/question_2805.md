# Q2805: ExchangeCreateActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeCreateActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` — where the attacker orders operands in ExchangeCreateActuator so ExchangeCreateActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that ExchangeCreateActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` -> `ExchangeCreateActuator.execute`
- Entrypoint: broadcast ExchangeCreateActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `ExchangeCreateActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in ExchangeCreateActuator so ExchangeCreateActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: ExchangeCreateActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
