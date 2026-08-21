# Q3475: IncrementalMerkleTreeContainer: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `IncrementalMerkleTreeContainer.wfcheck` in `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` — where the attacker submits a shielded proof or note to IncrementalMerkleTreeContainer.wfcheck with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that IncrementalMerkleTreeContainer.wfcheck binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` -> `IncrementalMerkleTreeContainer.wfcheck`
- Entrypoint: shielded transaction reaching IncrementalMerkleTreeContainer.wfcheck
- Attacker controls: request/transaction/contract inputs to `IncrementalMerkleTreeContainer.wfcheck` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to IncrementalMerkleTreeContainer.wfcheck with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: IncrementalMerkleTreeContainer.wfcheck binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
