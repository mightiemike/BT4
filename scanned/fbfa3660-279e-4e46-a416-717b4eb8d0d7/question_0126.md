# Q126: ByteArray: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toInt` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker sends a length-prefixed structure to ByteArray.toInt declaring a huge size, forcing a giant allocation — to break the invariant that ByteArray.toInt bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toInt`
- Entrypoint: encoded blob into ByteArray.toInt
- Attacker controls: request/transaction/contract inputs to `ByteArray.toInt` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to ByteArray.toInt declaring a huge size, forcing a giant allocation
- Invariant to test: ByteArray.toInt bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
