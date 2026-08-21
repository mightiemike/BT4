# Q1021: Sha256Hash: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.wrap` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker supplies an input where Sha256Hash.wrap skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that Sha256Hash.wrap rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.wrap`
- Entrypoint: address string into Sha256Hash.wrap
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.wrap` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where Sha256Hash.wrap skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: Sha256Hash.wrap rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
