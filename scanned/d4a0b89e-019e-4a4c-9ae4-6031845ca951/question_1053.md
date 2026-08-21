# Q1053: BIUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.sum` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker supplies bytes that BIUtil.sum sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that BIUtil.sum treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.sum`
- Entrypoint: bytes into BIUtil.sum
- Attacker controls: request/transaction/contract inputs to `BIUtil.sum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that BIUtil.sum sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: BIUtil.sum treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
