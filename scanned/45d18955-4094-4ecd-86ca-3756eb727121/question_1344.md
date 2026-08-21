# Q1344: BIUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.max` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker supplies bytes that BIUtil.max sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that BIUtil.max treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.max`
- Entrypoint: bytes into BIUtil.max
- Attacker controls: request/transaction/contract inputs to `BIUtil.max` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that BIUtil.max sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: BIUtil.max treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
