# Q3602: Sha256Hash: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.twiceOf` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker exploits Sha256Hash.twiceOf to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that Sha256Hash.twiceOf maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.twiceOf`
- Entrypoint: input flowing into Sha256Hash.twiceOf
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.twiceOf` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits Sha256Hash.twiceOf to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: Sha256Hash.twiceOf maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
