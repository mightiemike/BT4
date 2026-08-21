# Q2114: ByteArray: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.fromHexString` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker supplies an input where ByteArray.fromHexString skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ByteArray.fromHexString rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.fromHexString`
- Entrypoint: address string into ByteArray.fromHexString
- Attacker controls: request/transaction/contract inputs to `ByteArray.fromHexString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ByteArray.fromHexString skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ByteArray.fromHexString rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
