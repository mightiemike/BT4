# Q2423: ByteUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker supplies bytes that ByteUtil.parseBytes sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ByteUtil.parseBytes treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseBytes`
- Entrypoint: bytes into ByteUtil.parseBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ByteUtil.parseBytes sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ByteUtil.parseBytes treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
