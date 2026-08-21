# Q3634: VM: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VM.play` in `actuator/src/main/java/org/tron/core/vm/VM.java` — where the attacker finds an input to VM.play whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that VM.play is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VM.java` -> `VM.play`
- Entrypoint: contract exercising VM.play edge input
- Attacker controls: request/transaction/contract inputs to `VM.play` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to VM.play whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: VM.play is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
