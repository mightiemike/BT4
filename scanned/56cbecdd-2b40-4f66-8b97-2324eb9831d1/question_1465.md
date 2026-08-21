# Q1465: AbstractExchangeActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractExchangeActuator.addExact` in `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` — where the attacker orders operands in AbstractExchangeActuator so AbstractExchangeActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that AbstractExchangeActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` -> `AbstractExchangeActuator.addExact`
- Entrypoint: broadcast AbstractExchangeActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `AbstractExchangeActuator.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in AbstractExchangeActuator so AbstractExchangeActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: AbstractExchangeActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
