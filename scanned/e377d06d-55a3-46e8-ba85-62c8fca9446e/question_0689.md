# Q689: Program: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Program.setPreviouslyExecutedOp` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` — where the attacker finds an input to Program.setPreviouslyExecutedOp whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that Program.setPreviouslyExecutedOp is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Program.java` -> `Program.setPreviouslyExecutedOp`
- Entrypoint: contract exercising Program.setPreviouslyExecutedOp edge input
- Attacker controls: request/transaction/contract inputs to `Program.setPreviouslyExecutedOp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Program.setPreviouslyExecutedOp whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: Program.setPreviouslyExecutedOp is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
