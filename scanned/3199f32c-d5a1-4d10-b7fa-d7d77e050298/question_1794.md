# Q1794: IncrementalMerkleTreeContainer: native-lib param bounds

## Question
Can an unprivileged attacker (shielded transaction) abuse `IncrementalMerkleTreeContainer.wfcheck` in `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` — where the attacker sends oversized/malformed bytes to IncrementalMerkleTreeContainer.wfcheck that reach the rust/sodium library with unchecked length, crashing or corrupting the node — to break the invariant that IncrementalMerkleTreeContainer.wfcheck validates all lengths before the JNI/native call, leading to: Node RCE / crash (Fatal/Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` -> `IncrementalMerkleTreeContainer.wfcheck`
- Entrypoint: shielded param to IncrementalMerkleTreeContainer.wfcheck with bad length
- Attacker controls: request/transaction/contract inputs to `IncrementalMerkleTreeContainer.wfcheck` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends oversized/malformed bytes to IncrementalMerkleTreeContainer.wfcheck that reach the rust/sodium library with unchecked length, crashing or corrupting the node
- Invariant to test: IncrementalMerkleTreeContainer.wfcheck validates all lengths before the JNI/native call
- Expected Immunefi impact: Node RCE / crash (Fatal/Advanced)
- Fast validation: JUnit with malformed length asserting pre-call rejection
