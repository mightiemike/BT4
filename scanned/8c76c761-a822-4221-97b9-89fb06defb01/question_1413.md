# Q1413: Commons: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decode58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker sends a length-prefixed structure to Commons.decode58Check declaring a huge size, forcing a giant allocation — to break the invariant that Commons.decode58Check bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decode58Check`
- Entrypoint: encoded blob into Commons.decode58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Commons.decode58Check declaring a huge size, forcing a giant allocation
- Invariant to test: Commons.decode58Check bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
