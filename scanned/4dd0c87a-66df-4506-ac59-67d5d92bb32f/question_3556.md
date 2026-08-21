# Q3556: StrictMathWrapper: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.addExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker finds an input to StrictMathWrapper.addExact whose result differs by platform/rounding mode, diverging execution — to break the invariant that StrictMathWrapper.addExact yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.addExact`
- Entrypoint: value into StrictMathWrapper.addExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to StrictMathWrapper.addExact whose result differs by platform/rounding mode, diverging execution
- Invariant to test: StrictMathWrapper.addExact yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
