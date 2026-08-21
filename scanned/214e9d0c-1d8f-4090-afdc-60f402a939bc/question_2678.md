# Q2678: RLP: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeIP4Bytes` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker supplies an input where RLP.decodeIP4Bytes skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that RLP.decodeIP4Bytes rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeIP4Bytes`
- Entrypoint: address string into RLP.decodeIP4Bytes
- Attacker controls: request/transaction/contract inputs to `RLP.decodeIP4Bytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where RLP.decodeIP4Bytes skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: RLP.decodeIP4Bytes rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
