# Q2663: MerkleContainer: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `MerkleContainer.saveCmIntoMerkleTree` in `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` — where the attacker forces MerkleContainer.saveCmIntoMerkleTree to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in MerkleContainer.saveCmIntoMerkleTree are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` -> `MerkleContainer.saveCmIntoMerkleTree`
- Entrypoint: shielded input to MerkleContainer.saveCmIntoMerkleTree maximizing tree work
- Attacker controls: request/transaction/contract inputs to `MerkleContainer.saveCmIntoMerkleTree` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces MerkleContainer.saveCmIntoMerkleTree to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in MerkleContainer.saveCmIntoMerkleTree are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure MerkleContainer.saveCmIntoMerkleTree work vs charged cost
