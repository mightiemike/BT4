# Q944: StringUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `StringUtil.encode58Check` in `common/src/main/java/org/tron/common/utils/StringUtil.java` — where the attacker supplies an input where StringUtil.encode58Check skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that StringUtil.encode58Check rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/StringUtil.java` -> `StringUtil.encode58Check`
- Entrypoint: address string into StringUtil.encode58Check
- Attacker controls: request/transaction/contract inputs to `StringUtil.encode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where StringUtil.encode58Check skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: StringUtil.encode58Check rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
