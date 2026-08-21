# Q2289: StrictMathWrapper: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.min` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker sends a length-prefixed structure to StrictMathWrapper.min declaring a huge size, forcing a giant allocation — to break the invariant that StrictMathWrapper.min bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.min`
- Entrypoint: encoded blob into StrictMathWrapper.min
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.min` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to StrictMathWrapper.min declaring a huge size, forcing a giant allocation
- Invariant to test: StrictMathWrapper.min bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
