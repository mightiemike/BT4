# Q3606: Maths: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.multiplyExact` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker supplies bytes that Maths.multiplyExact sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Maths.multiplyExact treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.multiplyExact`
- Entrypoint: bytes into Maths.multiplyExact
- Attacker controls: request/transaction/contract inputs to `Maths.multiplyExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Maths.multiplyExact sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Maths.multiplyExact treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
