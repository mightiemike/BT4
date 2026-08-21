# Q3303: Maths: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.pow` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker sends a length-prefixed structure to Maths.pow declaring a huge size, forcing a giant allocation — to break the invariant that Maths.pow bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.pow`
- Entrypoint: encoded blob into Maths.pow
- Attacker controls: request/transaction/contract inputs to `Maths.pow` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Maths.pow declaring a huge size, forcing a giant allocation
- Invariant to test: Maths.pow bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
