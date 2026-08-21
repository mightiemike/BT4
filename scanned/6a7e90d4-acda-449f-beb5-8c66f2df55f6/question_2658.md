# Q2658: MerkleRoot: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createParentLeaves` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker exploits MerkleRoot.createParentLeaves to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that MerkleRoot.createParentLeaves maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createParentLeaves`
- Entrypoint: input flowing into MerkleRoot.createParentLeaves
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createParentLeaves` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits MerkleRoot.createParentLeaves to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: MerkleRoot.createParentLeaves maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
