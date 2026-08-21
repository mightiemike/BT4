# Q288: Base58: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `Base58.encode` in `common/src/main/java/org/tron/common/utils/Base58.java` — where the attacker sends a length-prefixed structure to Base58.encode declaring a huge size, forcing a giant allocation — to break the invariant that Base58.encode bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Base58.java` -> `Base58.encode`
- Entrypoint: encoded blob into Base58.encode
- Attacker controls: request/transaction/contract inputs to `Base58.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to Base58.encode declaring a huge size, forcing a giant allocation
- Invariant to test: Base58.encode bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
