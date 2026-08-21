# Q2823: Commons: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decode58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker supplies an input where Commons.decode58Check skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that Commons.decode58Check rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decode58Check`
- Entrypoint: address string into Commons.decode58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where Commons.decode58Check skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: Commons.decode58Check rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
