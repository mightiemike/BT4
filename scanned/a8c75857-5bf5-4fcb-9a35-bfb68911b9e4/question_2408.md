# Q2408: OperationRegistry: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV13OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker finds an input to OperationRegistry.newTronV13OperationSet whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that OperationRegistry.newTronV13OperationSet is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV13OperationSet`
- Entrypoint: contract exercising OperationRegistry.newTronV13OperationSet edge input
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV13OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to OperationRegistry.newTronV13OperationSet whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: OperationRegistry.newTronV13OperationSet is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
