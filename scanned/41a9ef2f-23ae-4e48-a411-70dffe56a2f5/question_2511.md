# Q2511: ByteUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.intToBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker supplies bytes that ByteUtil.intToBytes sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ByteUtil.intToBytes treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.intToBytes`
- Entrypoint: bytes into ByteUtil.intToBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.intToBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ByteUtil.intToBytes sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ByteUtil.intToBytes treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
