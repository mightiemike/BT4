# Q493: RLP: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeIP4Bytes` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker sends a length-prefixed structure to RLP.decodeIP4Bytes declaring a huge size, forcing a giant allocation — to break the invariant that RLP.decodeIP4Bytes bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeIP4Bytes`
- Entrypoint: encoded blob into RLP.decodeIP4Bytes
- Attacker controls: request/transaction/contract inputs to `RLP.decodeIP4Bytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to RLP.decodeIP4Bytes declaring a huge size, forcing a giant allocation
- Invariant to test: RLP.decodeIP4Bytes bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
