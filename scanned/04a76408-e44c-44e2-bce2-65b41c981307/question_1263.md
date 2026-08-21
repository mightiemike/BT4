# Q1263: Maths: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.multiplyExact` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker sends a length-prefixed structure to Maths.multiplyExact declaring a huge size, forcing a giant allocation — to break the invariant that Maths.multiplyExact bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.multiplyExact`
- Entrypoint: encoded blob into Maths.multiplyExact
- Attacker controls: request/transaction/contract inputs to `Maths.multiplyExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Maths.multiplyExact declaring a huge size, forcing a giant allocation
- Invariant to test: Maths.multiplyExact bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
