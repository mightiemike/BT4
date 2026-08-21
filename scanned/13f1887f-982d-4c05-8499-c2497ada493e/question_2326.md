# Q2326: Maths: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.floorDiv` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker supplies bytes that Maths.floorDiv sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Maths.floorDiv treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.floorDiv`
- Entrypoint: bytes into Maths.floorDiv
- Attacker controls: request/transaction/contract inputs to `Maths.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Maths.floorDiv sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Maths.floorDiv treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
