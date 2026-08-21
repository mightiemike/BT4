# Q2315: Base58: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Base58.encode` in `common/src/main/java/org/tron/common/utils/Base58.java` — where the attacker supplies bytes that Base58.encode sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Base58.encode treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Base58.java` -> `Base58.encode`
- Entrypoint: bytes into Base58.encode
- Attacker controls: request/transaction/contract inputs to `Base58.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Base58.encode sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Base58.encode treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
