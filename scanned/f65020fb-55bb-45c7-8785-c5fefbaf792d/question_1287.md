# Q1287: ByteArray: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.fromString` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker supplies an input where ByteArray.fromString skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ByteArray.fromString rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.fromString`
- Entrypoint: address string into ByteArray.fromString
- Attacker controls: request/transaction/contract inputs to `ByteArray.fromString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ByteArray.fromString skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ByteArray.fromString rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
