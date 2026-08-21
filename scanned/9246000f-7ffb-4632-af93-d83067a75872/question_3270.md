# Q3270: ByteArray: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.fromHexString` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker supplies bytes that ByteArray.fromHexString sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ByteArray.fromHexString treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.fromHexString`
- Entrypoint: bytes into ByteArray.fromHexString
- Attacker controls: request/transaction/contract inputs to `ByteArray.fromHexString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ByteArray.fromHexString sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ByteArray.fromHexString treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
