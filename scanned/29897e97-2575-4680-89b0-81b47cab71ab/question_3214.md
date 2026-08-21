# Q3214: ExchangeWithdrawActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeWithdrawActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` — where the attacker orders operands in ExchangeWithdrawActuator so ExchangeWithdrawActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that ExchangeWithdrawActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` -> `ExchangeWithdrawActuator.calcFee`
- Entrypoint: broadcast ExchangeWithdrawActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `ExchangeWithdrawActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in ExchangeWithdrawActuator so ExchangeWithdrawActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: ExchangeWithdrawActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
