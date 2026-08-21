# Q3853: MessageCall: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getOpCode` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker finds an input to MessageCall.getOpCode whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that MessageCall.getOpCode is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getOpCode`
- Entrypoint: contract exercising MessageCall.getOpCode edge input
- Attacker controls: request/transaction/contract inputs to `MessageCall.getOpCode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to MessageCall.getOpCode whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: MessageCall.getOpCode is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
