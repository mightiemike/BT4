# Q1672: MessageCall: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getCodeAddress` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker finds an input to MessageCall.getCodeAddress whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that MessageCall.getCodeAddress is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getCodeAddress`
- Entrypoint: contract exercising MessageCall.getCodeAddress edge input
- Attacker controls: request/transaction/contract inputs to `MessageCall.getCodeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to MessageCall.getCodeAddress whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: MessageCall.getCodeAddress is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
