# Q3105: OperationRegistry: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV15OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker finds an input to OperationRegistry.newTronV15OperationSet whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that OperationRegistry.newTronV15OperationSet is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV15OperationSet`
- Entrypoint: contract exercising OperationRegistry.newTronV15OperationSet edge input
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV15OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to OperationRegistry.newTronV15OperationSet whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: OperationRegistry.newTronV15OperationSet is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
