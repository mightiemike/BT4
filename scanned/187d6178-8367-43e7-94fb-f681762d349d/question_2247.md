# Q2247: BIUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.addSafely` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker sends a length-prefixed structure to BIUtil.addSafely declaring a huge size, forcing a giant allocation — to break the invariant that BIUtil.addSafely bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.addSafely`
- Entrypoint: encoded blob into BIUtil.addSafely
- Attacker controls: request/transaction/contract inputs to `BIUtil.addSafely` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to BIUtil.addSafely declaring a huge size, forcing a giant allocation
- Invariant to test: BIUtil.addSafely bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
