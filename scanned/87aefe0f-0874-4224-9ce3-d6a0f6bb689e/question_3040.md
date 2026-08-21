# Q3040: ByteUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseWord` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker sends a length-prefixed structure to ByteUtil.parseWord declaring a huge size, forcing a giant allocation — to break the invariant that ByteUtil.parseWord bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseWord`
- Entrypoint: encoded blob into ByteUtil.parseWord
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to ByteUtil.parseWord declaring a huge size, forcing a giant allocation
- Invariant to test: ByteUtil.parseWord bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
