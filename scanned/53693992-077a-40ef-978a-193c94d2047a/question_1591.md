# Q1591: StringUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `StringUtil.encode58Check` in `common/src/main/java/org/tron/common/utils/StringUtil.java` — where the attacker sends a length-prefixed structure to StringUtil.encode58Check declaring a huge size, forcing a giant allocation — to break the invariant that StringUtil.encode58Check bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/StringUtil.java` -> `StringUtil.encode58Check`
- Entrypoint: encoded blob into StringUtil.encode58Check
- Attacker controls: request/transaction/contract inputs to `StringUtil.encode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to StringUtil.encode58Check declaring a huge size, forcing a giant allocation
- Invariant to test: StringUtil.encode58Check bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
