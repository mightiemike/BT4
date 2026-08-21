# Q3371: ByteUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.bigIntegerToBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker finds an input to ByteUtil.bigIntegerToBytes whose result differs by platform/rounding mode, diverging execution — to break the invariant that ByteUtil.bigIntegerToBytes yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.bigIntegerToBytes`
- Entrypoint: value into ByteUtil.bigIntegerToBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.bigIntegerToBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ByteUtil.bigIntegerToBytes whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ByteUtil.bigIntegerToBytes yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
