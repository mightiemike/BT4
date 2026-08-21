# Q386: BIUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.sum` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker finds an input to BIUtil.sum whose result differs by platform/rounding mode, diverging execution — to break the invariant that BIUtil.sum yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.sum`
- Entrypoint: value into BIUtil.sum
- Attacker controls: request/transaction/contract inputs to `BIUtil.sum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to BIUtil.sum whose result differs by platform/rounding mode, diverging execution
- Invariant to test: BIUtil.sum yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
