# Q3415: CompactEncoder: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.packNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker sends a length-prefixed structure to CompactEncoder.packNibbles declaring a huge size, forcing a giant allocation — to break the invariant that CompactEncoder.packNibbles bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.packNibbles`
- Entrypoint: encoded blob into CompactEncoder.packNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.packNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to CompactEncoder.packNibbles declaring a huge size, forcing a giant allocation
- Invariant to test: CompactEncoder.packNibbles bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
