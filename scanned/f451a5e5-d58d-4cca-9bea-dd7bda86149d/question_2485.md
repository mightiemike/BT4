# Q2485: ByteUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.intToBytesNoLeadZeroes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker finds an input to ByteUtil.intToBytesNoLeadZeroes whose result differs by platform/rounding mode, diverging execution — to break the invariant that ByteUtil.intToBytesNoLeadZeroes yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.intToBytesNoLeadZeroes`
- Entrypoint: value into ByteUtil.intToBytesNoLeadZeroes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.intToBytesNoLeadZeroes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ByteUtil.intToBytesNoLeadZeroes whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ByteUtil.intToBytesNoLeadZeroes yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
