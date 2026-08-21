# Q2966: TransactionUtil: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionUtil.generateContractAddress` in `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` — where the attacker floods cheap transactions that TransactionUtil.generateContractAddress admits and holds, exhausting pending memory — to break the invariant that pending admission in TransactionUtil.generateContractAddress is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java` -> `TransactionUtil.generateContractAddress`
- Entrypoint: flood pending via TransactionUtil.generateContractAddress
- Attacker controls: request/transaction/contract inputs to `TransactionUtil.generateContractAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that TransactionUtil.generateContractAddress admits and holds, exhausting pending memory
- Invariant to test: pending admission in TransactionUtil.generateContractAddress is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
