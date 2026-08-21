# Q3423: RLP: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeInt` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker supplies an input where RLP.decodeInt skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that RLP.decodeInt rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeInt`
- Entrypoint: address string into RLP.decodeInt
- Attacker controls: request/transaction/contract inputs to `RLP.decodeInt` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where RLP.decodeInt skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: RLP.decodeInt rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
