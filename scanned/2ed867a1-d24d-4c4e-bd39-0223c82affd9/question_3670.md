# Q3670: ExchangeTransactionActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeTransactionActuator.doValidate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` — where the attacker orders operands in ExchangeTransactionActuator so ExchangeTransactionActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that ExchangeTransactionActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` -> `ExchangeTransactionActuator.doValidate`
- Entrypoint: broadcast ExchangeTransactionActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `ExchangeTransactionActuator.doValidate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in ExchangeTransactionActuator so ExchangeTransactionActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: ExchangeTransactionActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
