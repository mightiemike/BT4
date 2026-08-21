# Q998: CommonParameter: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.calcMaxTimeRatio` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker supplies an input where CommonParameter.calcMaxTimeRatio skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that CommonParameter.calcMaxTimeRatio rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.calcMaxTimeRatio`
- Entrypoint: address string into CommonParameter.calcMaxTimeRatio
- Attacker controls: request/transaction/contract inputs to `CommonParameter.calcMaxTimeRatio` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where CommonParameter.calcMaxTimeRatio skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: CommonParameter.calcMaxTimeRatio rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
