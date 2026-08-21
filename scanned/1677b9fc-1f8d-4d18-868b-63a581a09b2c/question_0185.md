# Q185: Base58: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `Base58.decode` in `common/src/main/java/org/tron/common/utils/Base58.java` — where the attacker supplies an input where Base58.decode skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that Base58.decode rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Base58.java` -> `Base58.decode`
- Entrypoint: address string into Base58.decode
- Attacker controls: request/transaction/contract inputs to `Base58.decode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where Base58.decode skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: Base58.decode rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
