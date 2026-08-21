# Q1441: Sha256Hash: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.newSM3Digest` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker sends a length-prefixed structure to Sha256Hash.newSM3Digest declaring a huge size, forcing a giant allocation — to break the invariant that Sha256Hash.newSM3Digest bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.newSM3Digest`
- Entrypoint: encoded blob into Sha256Hash.newSM3Digest
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.newSM3Digest` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Sha256Hash.newSM3Digest declaring a huge size, forcing a giant allocation
- Invariant to test: Sha256Hash.newSM3Digest bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
