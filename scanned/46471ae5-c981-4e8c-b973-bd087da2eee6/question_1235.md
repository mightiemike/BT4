# Q1235: DecodeUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `DecodeUtil.addressValid` in `common/src/main/java/org/tron/common/utils/DecodeUtil.java` — where the attacker sends a length-prefixed structure to DecodeUtil.addressValid declaring a huge size, forcing a giant allocation — to break the invariant that DecodeUtil.addressValid bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/DecodeUtil.java` -> `DecodeUtil.addressValid`
- Entrypoint: encoded blob into DecodeUtil.addressValid
- Attacker controls: request/transaction/contract inputs to `DecodeUtil.addressValid` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to DecodeUtil.addressValid declaring a huge size, forcing a giant allocation
- Invariant to test: DecodeUtil.addressValid bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
