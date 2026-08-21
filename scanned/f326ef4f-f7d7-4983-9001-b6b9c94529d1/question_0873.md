# Q873: ByteArray: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toLong` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker finds an input to ByteArray.toLong whose result differs by platform/rounding mode, diverging execution — to break the invariant that ByteArray.toLong yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toLong`
- Entrypoint: value into ByteArray.toLong
- Attacker controls: request/transaction/contract inputs to `ByteArray.toLong` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ByteArray.toLong whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ByteArray.toLong yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
