# Q1078: ContractState: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ContractState.createAccount` in `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` — where the attacker finds an input to ContractState.createAccount whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that ContractState.createAccount is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` -> `ContractState.createAccount`
- Entrypoint: contract exercising ContractState.createAccount edge input
- Attacker controls: request/transaction/contract inputs to `ContractState.createAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ContractState.createAccount whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: ContractState.createAccount is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
