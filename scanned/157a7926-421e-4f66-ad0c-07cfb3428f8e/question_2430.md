# Q2430: Maths: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.addExact` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker finds an input to Maths.addExact whose result differs by platform/rounding mode, diverging execution — to break the invariant that Maths.addExact yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.addExact`
- Entrypoint: value into Maths.addExact
- Attacker controls: request/transaction/contract inputs to `Maths.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Maths.addExact whose result differs by platform/rounding mode, diverging execution
- Invariant to test: Maths.addExact yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
