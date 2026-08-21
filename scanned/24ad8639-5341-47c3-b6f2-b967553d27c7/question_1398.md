# Q1398: RLP: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.encode` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker finds an input to RLP.encode whose result differs by platform/rounding mode, diverging execution — to break the invariant that RLP.encode yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.encode`
- Entrypoint: value into RLP.encode
- Attacker controls: request/transaction/contract inputs to `RLP.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to RLP.encode whose result differs by platform/rounding mode, diverging execution
- Invariant to test: RLP.encode yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
