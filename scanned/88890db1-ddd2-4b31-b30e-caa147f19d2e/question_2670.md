# Q2670: Maths: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.floorDiv` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker finds an input to Maths.floorDiv whose result differs by platform/rounding mode, diverging execution — to break the invariant that Maths.floorDiv yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.floorDiv`
- Entrypoint: value into Maths.floorDiv
- Attacker controls: request/transaction/contract inputs to `Maths.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Maths.floorDiv whose result differs by platform/rounding mode, diverging execution
- Invariant to test: Maths.floorDiv yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
