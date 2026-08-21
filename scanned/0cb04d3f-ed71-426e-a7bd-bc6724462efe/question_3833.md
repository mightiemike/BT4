# Q3833: StrictMathWrapper: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.multiplyExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker supplies bytes that StrictMathWrapper.multiplyExact sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that StrictMathWrapper.multiplyExact treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.multiplyExact`
- Entrypoint: bytes into StrictMathWrapper.multiplyExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.multiplyExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that StrictMathWrapper.multiplyExact sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: StrictMathWrapper.multiplyExact treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
