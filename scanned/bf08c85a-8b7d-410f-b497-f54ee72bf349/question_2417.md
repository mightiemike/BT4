# Q2417: BIUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.max` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker finds an input to BIUtil.max whose result differs by platform/rounding mode, diverging execution — to break the invariant that BIUtil.max yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.max`
- Entrypoint: value into BIUtil.max
- Attacker controls: request/transaction/contract inputs to `BIUtil.max` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to BIUtil.max whose result differs by platform/rounding mode, diverging execution
- Invariant to test: BIUtil.max yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
