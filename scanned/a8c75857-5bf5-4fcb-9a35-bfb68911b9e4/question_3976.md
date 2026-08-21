# Q3976: ForkController: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passNew` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker finds an input to ForkController.passNew whose result differs by platform/rounding mode, diverging execution — to break the invariant that ForkController.passNew yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passNew`
- Entrypoint: value into ForkController.passNew
- Attacker controls: request/transaction/contract inputs to `ForkController.passNew` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ForkController.passNew whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ForkController.passNew yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
