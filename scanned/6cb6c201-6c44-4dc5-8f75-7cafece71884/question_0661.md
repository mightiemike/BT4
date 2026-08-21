# Q661: StrictMathWrapper: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.pow` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker finds an input to StrictMathWrapper.pow whose result differs by platform/rounding mode, diverging execution — to break the invariant that StrictMathWrapper.pow yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.pow`
- Entrypoint: value into StrictMathWrapper.pow
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.pow` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to StrictMathWrapper.pow whose result differs by platform/rounding mode, diverging execution
- Invariant to test: StrictMathWrapper.pow yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
