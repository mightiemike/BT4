# Q3053: StrictMathWrapper: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.min` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker finds an input to StrictMathWrapper.min whose result differs by platform/rounding mode, diverging execution — to break the invariant that StrictMathWrapper.min yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.min`
- Entrypoint: value into StrictMathWrapper.min
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.min` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to StrictMathWrapper.min whose result differs by platform/rounding mode, diverging execution
- Invariant to test: StrictMathWrapper.min yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
