# Q269: StrictMathWrapper: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.pow` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker supplies bytes that StrictMathWrapper.pow sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that StrictMathWrapper.pow treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.pow`
- Entrypoint: bytes into StrictMathWrapper.pow
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.pow` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that StrictMathWrapper.pow sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: StrictMathWrapper.pow treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
