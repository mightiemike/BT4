# Q1543: ByteArray: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toHexString` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker finds an input to ByteArray.toHexString whose result differs by platform/rounding mode, diverging execution — to break the invariant that ByteArray.toHexString yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toHexString`
- Entrypoint: value into ByteArray.toHexString
- Attacker controls: request/transaction/contract inputs to `ByteArray.toHexString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ByteArray.toHexString whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ByteArray.toHexString yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
