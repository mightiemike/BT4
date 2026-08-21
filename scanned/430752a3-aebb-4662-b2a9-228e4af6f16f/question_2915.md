# Q2915: StrictMathWrapper: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.subtractExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker supplies bytes that StrictMathWrapper.subtractExact sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that StrictMathWrapper.subtractExact treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.subtractExact`
- Entrypoint: bytes into StrictMathWrapper.subtractExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.subtractExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that StrictMathWrapper.subtractExact sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: StrictMathWrapper.subtractExact treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
