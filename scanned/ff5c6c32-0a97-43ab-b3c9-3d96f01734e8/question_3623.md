# Q3623: CommonParameter: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.calcMaxTimeRatio` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker finds an input to CommonParameter.calcMaxTimeRatio whose result differs by platform/rounding mode, diverging execution — to break the invariant that CommonParameter.calcMaxTimeRatio yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.calcMaxTimeRatio`
- Entrypoint: value into CommonParameter.calcMaxTimeRatio
- Attacker controls: request/transaction/contract inputs to `CommonParameter.calcMaxTimeRatio` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to CommonParameter.calcMaxTimeRatio whose result differs by platform/rounding mode, diverging execution
- Invariant to test: CommonParameter.calcMaxTimeRatio yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
