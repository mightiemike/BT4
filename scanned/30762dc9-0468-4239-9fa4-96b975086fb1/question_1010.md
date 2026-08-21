# Q1010: Commons: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decodeFromBase58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker supplies an input where Commons.decodeFromBase58Check skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that Commons.decodeFromBase58Check rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decodeFromBase58Check`
- Entrypoint: address string into Commons.decodeFromBase58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decodeFromBase58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where Commons.decodeFromBase58Check skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: Commons.decodeFromBase58Check rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
