# Q787: Commons: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decodeFromBase58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker sends a length-prefixed structure to Commons.decodeFromBase58Check declaring a huge size, forcing a giant allocation — to break the invariant that Commons.decodeFromBase58Check bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decodeFromBase58Check`
- Entrypoint: encoded blob into Commons.decodeFromBase58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decodeFromBase58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Commons.decodeFromBase58Check declaring a huge size, forcing a giant allocation
- Invariant to test: Commons.decodeFromBase58Check bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
