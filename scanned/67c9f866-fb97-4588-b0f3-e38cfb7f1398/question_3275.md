# Q3275: Sha256Hash: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.newSM3Digest` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker supplies bytes that Sha256Hash.newSM3Digest sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Sha256Hash.newSM3Digest treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.newSM3Digest`
- Entrypoint: bytes into Sha256Hash.newSM3Digest
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.newSM3Digest` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Sha256Hash.newSM3Digest sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Sha256Hash.newSM3Digest treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
