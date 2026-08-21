# Q203: RLP: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeList` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker finds an input to RLP.decodeList whose result differs by platform/rounding mode, diverging execution — to break the invariant that RLP.decodeList yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeList`
- Entrypoint: value into RLP.decodeList
- Attacker controls: request/transaction/contract inputs to `RLP.decodeList` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to RLP.decodeList whose result differs by platform/rounding mode, diverging execution
- Invariant to test: RLP.decodeList yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
