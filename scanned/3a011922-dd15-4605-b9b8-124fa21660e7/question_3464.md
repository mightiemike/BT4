# Q3464: RLP: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeInt` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker sends a length-prefixed structure to RLP.decodeInt declaring a huge size, forcing a giant allocation — to break the invariant that RLP.decodeInt bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeInt`
- Entrypoint: encoded blob into RLP.decodeInt
- Attacker controls: request/transaction/contract inputs to `RLP.decodeInt` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to RLP.decodeInt declaring a huge size, forcing a giant allocation
- Invariant to test: RLP.decodeInt bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
