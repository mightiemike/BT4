# Q1530: OperationActions: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationActions.subAction` in `actuator/src/main/java/org/tron/core/vm/OperationActions.java` — where the attacker finds an input to OperationActions.subAction whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that OperationActions.subAction is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationActions.java` -> `OperationActions.subAction`
- Entrypoint: contract exercising OperationActions.subAction edge input
- Attacker controls: request/transaction/contract inputs to `OperationActions.subAction` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to OperationActions.subAction whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: OperationActions.subAction is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
