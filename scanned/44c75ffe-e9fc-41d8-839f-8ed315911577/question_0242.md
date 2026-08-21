# Q242: CommonParameter: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.reset` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker supplies bytes that CommonParameter.reset sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that CommonParameter.reset treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.reset`
- Entrypoint: bytes into CommonParameter.reset
- Attacker controls: request/transaction/contract inputs to `CommonParameter.reset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that CommonParameter.reset sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: CommonParameter.reset treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
