# Q3876: ProposalUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ProposalUtil.validator` in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` — where the attacker supplies an input where ProposalUtil.validator skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ProposalUtil.validator rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` -> `ProposalUtil.validator`
- Entrypoint: address string into ProposalUtil.validator
- Attacker controls: request/transaction/contract inputs to `ProposalUtil.validator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ProposalUtil.validator skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ProposalUtil.validator rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
