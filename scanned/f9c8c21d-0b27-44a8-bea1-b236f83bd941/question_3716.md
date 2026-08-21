# Q3716: MerkleRoot: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.root` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker exploits MerkleRoot.root to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that MerkleRoot.root maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.root`
- Entrypoint: input flowing into MerkleRoot.root
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.root` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits MerkleRoot.root to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: MerkleRoot.root maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
