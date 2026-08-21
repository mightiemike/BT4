# Q1607: ExchangeInjectActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeInjectActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` — where the attacker orders operands in ExchangeInjectActuator so ExchangeInjectActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that ExchangeInjectActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` -> `ExchangeInjectActuator.validate`
- Entrypoint: broadcast ExchangeInjectActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `ExchangeInjectActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in ExchangeInjectActuator so ExchangeInjectActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: ExchangeInjectActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
