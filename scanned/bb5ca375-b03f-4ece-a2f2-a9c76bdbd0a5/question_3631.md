# Q3631: ForkController: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passOld` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker finds an input to ForkController.passOld whose result differs by platform/rounding mode, diverging execution — to break the invariant that ForkController.passOld yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passOld`
- Entrypoint: value into ForkController.passOld
- Attacker controls: request/transaction/contract inputs to `ForkController.passOld` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ForkController.passOld whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ForkController.passOld yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
