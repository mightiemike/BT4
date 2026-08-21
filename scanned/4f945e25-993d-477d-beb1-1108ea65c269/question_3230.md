# Q3230: IncrementalMerkleTreeContainer: merkle tree unbounded work

## Question
Can an unprivileged attacker (shielded transaction) abuse `IncrementalMerkleTreeContainer.wfcheck` in `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` — where the attacker forces IncrementalMerkleTreeContainer.wfcheck to build or walk an oversized merkle structure for cheap input — to break the invariant that tree operations in IncrementalMerkleTreeContainer.wfcheck are bounded by fee/energy, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` -> `IncrementalMerkleTreeContainer.wfcheck`
- Entrypoint: shielded input to IncrementalMerkleTreeContainer.wfcheck maximizing tree work
- Attacker controls: request/transaction/contract inputs to `IncrementalMerkleTreeContainer.wfcheck` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces IncrementalMerkleTreeContainer.wfcheck to build or walk an oversized merkle structure for cheap input
- Invariant to test: tree operations in IncrementalMerkleTreeContainer.wfcheck are bounded by fee/energy
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: measure IncrementalMerkleTreeContainer.wfcheck work vs charged cost
