# Q1598: BIUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.toBI` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker sends a length-prefixed structure to BIUtil.toBI declaring a huge size, forcing a giant allocation — to break the invariant that BIUtil.toBI bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.toBI`
- Entrypoint: encoded blob into BIUtil.toBI
- Attacker controls: request/transaction/contract inputs to `BIUtil.toBI` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to BIUtil.toBI declaring a huge size, forcing a giant allocation
- Invariant to test: BIUtil.toBI bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
