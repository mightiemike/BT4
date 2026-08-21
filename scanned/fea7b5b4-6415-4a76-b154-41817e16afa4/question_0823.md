# Q823: MerkleContainer: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `MerkleContainer.saveCmIntoMerkleTree` in `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` — where the attacker sends oversized/malformed bytes to MerkleContainer.saveCmIntoMerkleTree that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that MerkleContainer.saveCmIntoMerkleTree validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` -> `MerkleContainer.saveCmIntoMerkleTree`
- Entrypoint: shielded param to MerkleContainer.saveCmIntoMerkleTree with bad length
- Attacker controls: request/transaction/contract inputs to `MerkleContainer.saveCmIntoMerkleTree` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to MerkleContainer.saveCmIntoMerkleTree that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: MerkleContainer.saveCmIntoMerkleTree validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
