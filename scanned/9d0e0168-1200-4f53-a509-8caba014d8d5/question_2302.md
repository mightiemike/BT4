# Q2302: BIUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.addSafely` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker supplies an input where BIUtil.addSafely skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that BIUtil.addSafely rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.addSafely`
- Entrypoint: address string into BIUtil.addSafely
- Attacker controls: request/transaction/contract inputs to `BIUtil.addSafely` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where BIUtil.addSafely skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: BIUtil.addSafely rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
