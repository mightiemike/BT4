# Q967: Sha256Hash: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.create` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker sends a length-prefixed structure to Sha256Hash.create declaring a huge size, forcing a giant allocation — to break the invariant that Sha256Hash.create bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.create`
- Entrypoint: encoded blob into Sha256Hash.create
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Sha256Hash.create declaring a huge size, forcing a giant allocation
- Invariant to test: Sha256Hash.create bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
