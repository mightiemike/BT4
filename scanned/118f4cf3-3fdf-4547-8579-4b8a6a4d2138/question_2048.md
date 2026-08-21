# Q2048: Memory: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Memory.readWord` in `actuator/src/main/java/org/tron/core/vm/program/Memory.java` — where the attacker finds an input to Memory.readWord whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that Memory.readWord is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Memory.java` -> `Memory.readWord`
- Entrypoint: contract exercising Memory.readWord edge input
- Attacker controls: request/transaction/contract inputs to `Memory.readWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Memory.readWord whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: Memory.readWord is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
