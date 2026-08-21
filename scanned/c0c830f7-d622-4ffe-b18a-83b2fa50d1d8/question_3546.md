# Q3546: Stack: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Stack.pop` in `actuator/src/main/java/org/tron/core/vm/program/Stack.java` — where the attacker finds an input to Stack.pop whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that Stack.pop is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Stack.java` -> `Stack.pop`
- Entrypoint: contract exercising Stack.pop edge input
- Attacker controls: request/transaction/contract inputs to `Stack.pop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Stack.pop whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: Stack.pop is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
