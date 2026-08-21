# Q2179: CompactEncoder: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.packNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker supplies bytes that CompactEncoder.packNibbles sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that CompactEncoder.packNibbles treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.packNibbles`
- Entrypoint: bytes into CompactEncoder.packNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.packNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that CompactEncoder.packNibbles sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: CompactEncoder.packNibbles treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
