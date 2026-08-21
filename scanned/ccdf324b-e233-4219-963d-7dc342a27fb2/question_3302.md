# Q3302: BIUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.max` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker sends a length-prefixed structure to BIUtil.max declaring a huge size, forcing a giant allocation — to break the invariant that BIUtil.max bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.max`
- Entrypoint: encoded blob into BIUtil.max
- Attacker controls: request/transaction/contract inputs to `BIUtil.max` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to BIUtil.max declaring a huge size, forcing a giant allocation
- Invariant to test: BIUtil.max bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
