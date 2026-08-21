# Q751: MerkleRoot: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createLeaf` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker supplies an input where MerkleRoot.createLeaf skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that MerkleRoot.createLeaf rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createLeaf`
- Entrypoint: address string into MerkleRoot.createLeaf
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createLeaf` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where MerkleRoot.createLeaf skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: MerkleRoot.createLeaf rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
