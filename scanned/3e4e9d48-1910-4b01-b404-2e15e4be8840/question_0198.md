# Q198: MerkleContainer: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `MerkleContainer.resetCurrentMerkleTree` in `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` — where the attacker replays a nullifier or stale anchor through MerkleContainer.resetCurrentMerkleTree to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in MerkleContainer.resetCurrentMerkleTree, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` -> `MerkleContainer.resetCurrentMerkleTree`
- Entrypoint: shielded spend to MerkleContainer.resetCurrentMerkleTree with reused nullifier
- Attacker controls: request/transaction/contract inputs to `MerkleContainer.resetCurrentMerkleTree` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through MerkleContainer.resetCurrentMerkleTree to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in MerkleContainer.resetCurrentMerkleTree
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
