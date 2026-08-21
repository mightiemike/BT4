# Q2706: Commons: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decode58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker supplies bytes that Commons.decode58Check sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Commons.decode58Check treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decode58Check`
- Entrypoint: bytes into Commons.decode58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Commons.decode58Check sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Commons.decode58Check treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
