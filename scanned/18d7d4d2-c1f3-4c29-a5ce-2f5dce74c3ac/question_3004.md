# Q3004: ByteUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.bigIntegerToBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker supplies an input where ByteUtil.bigIntegerToBytes skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ByteUtil.bigIntegerToBytes rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.bigIntegerToBytes`
- Entrypoint: address string into ByteUtil.bigIntegerToBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.bigIntegerToBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ByteUtil.bigIntegerToBytes skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ByteUtil.bigIntegerToBytes rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
