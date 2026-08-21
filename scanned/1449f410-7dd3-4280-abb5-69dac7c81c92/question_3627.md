# Q3627: MerkleContainer: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `MerkleContainer.merkleRootExist` in `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` — where the attacker submits a shielded proof or note to MerkleContainer.merkleRootExist with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that MerkleContainer.merkleRootExist binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` -> `MerkleContainer.merkleRootExist`
- Entrypoint: shielded transaction reaching MerkleContainer.merkleRootExist
- Attacker controls: request/transaction/contract inputs to `MerkleContainer.merkleRootExist` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to MerkleContainer.merkleRootExist with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: MerkleContainer.merkleRootExist binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
