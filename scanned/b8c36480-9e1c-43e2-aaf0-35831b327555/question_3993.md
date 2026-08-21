# Q3993: BIUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.toBI` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker supplies an input where BIUtil.toBI skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that BIUtil.toBI rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.toBI`
- Entrypoint: address string into BIUtil.toBI
- Attacker controls: request/transaction/contract inputs to `BIUtil.toBI` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where BIUtil.toBI skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: BIUtil.toBI rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
