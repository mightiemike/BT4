# Q3714: RLP: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeBigInteger` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker supplies bytes that RLP.decodeBigInteger sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that RLP.decodeBigInteger treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeBigInteger`
- Entrypoint: bytes into RLP.decodeBigInteger
- Attacker controls: request/transaction/contract inputs to `RLP.decodeBigInteger` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that RLP.decodeBigInteger sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: RLP.decodeBigInteger treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
