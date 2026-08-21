# Q842: VMUtils: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VMUtils.write` in `actuator/src/main/java/org/tron/core/vm/VMUtils.java` — where the attacker finds an input to VMUtils.write whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that VMUtils.write is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VMUtils.java` -> `VMUtils.write`
- Entrypoint: contract exercising VMUtils.write edge input
- Attacker controls: request/transaction/contract inputs to `VMUtils.write` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to VMUtils.write whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: VMUtils.write is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
