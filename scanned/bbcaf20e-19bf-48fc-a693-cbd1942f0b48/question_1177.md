# Q1177: Sha256Hash: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.newDigest` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker exploits Sha256Hash.newDigest to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that Sha256Hash.newDigest maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.newDigest`
- Entrypoint: input flowing into Sha256Hash.newDigest
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.newDigest` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits Sha256Hash.newDigest to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: Sha256Hash.newDigest maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
