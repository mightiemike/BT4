# Q3984: MerkleContainer: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `MerkleContainer.merkleRootExist` in `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` — where the attacker sends oversized/malformed bytes to MerkleContainer.merkleRootExist that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that MerkleContainer.merkleRootExist validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java` -> `MerkleContainer.merkleRootExist`
- Entrypoint: shielded param to MerkleContainer.merkleRootExist with bad length
- Attacker controls: request/transaction/contract inputs to `MerkleContainer.merkleRootExist` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to MerkleContainer.merkleRootExist that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: MerkleContainer.merkleRootExist validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
