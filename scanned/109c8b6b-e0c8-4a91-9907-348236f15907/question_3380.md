# Q3380: ByteArray: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toInt` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker finds an input to ByteArray.toInt whose result differs by platform/rounding mode, diverging execution — to break the invariant that ByteArray.toInt yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toInt`
- Entrypoint: value into ByteArray.toInt
- Attacker controls: request/transaction/contract inputs to `ByteArray.toInt` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ByteArray.toInt whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ByteArray.toInt yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
