# Q2985: Storage: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.put` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker finds an input to Storage.put whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that Storage.put is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.put`
- Entrypoint: contract exercising Storage.put edge input
- Attacker controls: request/transaction/contract inputs to `Storage.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Storage.put whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: Storage.put is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
