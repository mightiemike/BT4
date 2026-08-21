# Q1728: MerkleRoot: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.root` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker supplies an input where MerkleRoot.root skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that MerkleRoot.root rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.root`
- Entrypoint: address string into MerkleRoot.root
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.root` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where MerkleRoot.root skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: MerkleRoot.root rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
