# Q1951: Bech32: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `Bech32.encode` in `common/src/main/java/org/tron/common/utils/Bech32.java` — where the attacker supplies an input where Bech32.encode skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that Bech32.encode rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Bech32.java` -> `Bech32.encode`
- Entrypoint: address string into Bech32.encode
- Attacker controls: request/transaction/contract inputs to `Bech32.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where Bech32.encode skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: Bech32.encode rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
