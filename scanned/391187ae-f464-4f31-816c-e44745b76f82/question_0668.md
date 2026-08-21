# Q668: RepositoryImpl: non-deterministic result

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `RepositoryImpl.newRepositoryChild` in `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` — where the attacker finds an input to RepositoryImpl.newRepositoryChild whose output depends on JDK/platform/iteration order, diverging node state — to break the invariant that RepositoryImpl.newRepositoryChild is deterministic across JDK and node, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` -> `RepositoryImpl.newRepositoryChild`
- Entrypoint: contract exercising RepositoryImpl.newRepositoryChild edge input
- Attacker controls: request/transaction/contract inputs to `RepositoryImpl.newRepositoryChild` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to RepositoryImpl.newRepositoryChild whose output depends on JDK/platform/iteration order, diverging node state
- Invariant to test: RepositoryImpl.newRepositoryChild is deterministic across JDK and node
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential run across JDK8/17 asserting identical result
