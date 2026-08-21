# Q3298: ByteUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker sends a length-prefixed structure to ByteUtil.parseBytes declaring a huge size, forcing a giant allocation — to break the invariant that ByteUtil.parseBytes bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseBytes`
- Entrypoint: encoded blob into ByteUtil.parseBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to ByteUtil.parseBytes declaring a huge size, forcing a giant allocation
- Invariant to test: ByteUtil.parseBytes bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
