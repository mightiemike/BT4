# Q3346: ContractState: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ContractState.putNewContract` in `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` — where the attacker finds an input to ContractState.putNewContract whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that ContractState.putNewContract is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/ContractState.java` -> `ContractState.putNewContract`
- Entrypoint: contract exercising ContractState.putNewContract edge input
- Attacker controls: request/transaction/contract inputs to `ContractState.putNewContract` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ContractState.putNewContract whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: ContractState.putNewContract is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
