# Q3024: BIUtil: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.toBI` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker exploits BIUtil.toBI to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that BIUtil.toBI maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.toBI`
- Entrypoint: input flowing into BIUtil.toBI
- Attacker controls: request/transaction/contract inputs to `BIUtil.toBI` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits BIUtil.toBI to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: BIUtil.toBI maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
