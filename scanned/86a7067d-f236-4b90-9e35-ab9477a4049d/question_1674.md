# Q1674: TransactionRegister: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionRegister.registerActuator` in `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` — where the attacker floods cheap transactions that TransactionRegister.registerActuator admits and holds, exhausting pending memory — to break the invariant that pending admission in TransactionRegister.registerActuator is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` -> `TransactionRegister.registerActuator`
- Entrypoint: flood pending via TransactionRegister.registerActuator
- Attacker controls: request/transaction/contract inputs to `TransactionRegister.registerActuator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that TransactionRegister.registerActuator admits and holds, exhausting pending memory
- Invariant to test: pending admission in TransactionRegister.registerActuator is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
