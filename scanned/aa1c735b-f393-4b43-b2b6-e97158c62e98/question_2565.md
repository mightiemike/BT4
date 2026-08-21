# Q2565: ByteArray: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toInt` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker supplies bytes that ByteArray.toInt sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ByteArray.toInt treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toInt`
- Entrypoint: bytes into ByteArray.toInt
- Attacker controls: request/transaction/contract inputs to `ByteArray.toInt` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ByteArray.toInt sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ByteArray.toInt treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
