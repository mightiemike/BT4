# Q3095: Commons: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decodeFromBase58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker supplies bytes that Commons.decodeFromBase58Check sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Commons.decodeFromBase58Check treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decodeFromBase58Check`
- Entrypoint: bytes into Commons.decodeFromBase58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decodeFromBase58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Commons.decodeFromBase58Check sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Commons.decodeFromBase58Check treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
