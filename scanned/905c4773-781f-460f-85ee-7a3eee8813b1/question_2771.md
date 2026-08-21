# Q2771: Maths: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.multiplyExact` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker finds an input to Maths.multiplyExact whose result differs by platform/rounding mode, diverging execution — to break the invariant that Maths.multiplyExact yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.multiplyExact`
- Entrypoint: value into Maths.multiplyExact
- Attacker controls: request/transaction/contract inputs to `Maths.multiplyExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Maths.multiplyExact whose result differs by platform/rounding mode, diverging execution
- Invariant to test: Maths.multiplyExact yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
