# Q294: InternalTransaction: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `InternalTransaction.reject` in `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` — where the attacker finds an input to InternalTransaction.reject whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that InternalTransaction.reject is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` -> `InternalTransaction.reject`
- Entrypoint: contract exercising InternalTransaction.reject edge input
- Attacker controls: request/transaction/contract inputs to `InternalTransaction.reject` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to InternalTransaction.reject whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: InternalTransaction.reject is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
