# Q3458: Maths: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.addExact` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker sends a length-prefixed structure to Maths.addExact declaring a huge size, forcing a giant allocation — to break the invariant that Maths.addExact bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.addExact`
- Entrypoint: encoded blob into Maths.addExact
- Attacker controls: request/transaction/contract inputs to `Maths.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Maths.addExact declaring a huge size, forcing a giant allocation
- Invariant to test: Maths.addExact bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
