# Q199: ByteUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker finds an input to ByteUtil.parseBytes whose result differs by platform/rounding mode, diverging execution — to break the invariant that ByteUtil.parseBytes yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseBytes`
- Entrypoint: value into ByteUtil.parseBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ByteUtil.parseBytes whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ByteUtil.parseBytes yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
