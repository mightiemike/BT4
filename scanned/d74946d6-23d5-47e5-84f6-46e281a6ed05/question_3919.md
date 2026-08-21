# Q3919: IncrementalMerkleTreeContainer: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `IncrementalMerkleTreeContainer.append` in `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` — where the attacker replays a nullifier or stale anchor through IncrementalMerkleTreeContainer.append to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in IncrementalMerkleTreeContainer.append, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` -> `IncrementalMerkleTreeContainer.append`
- Entrypoint: shielded spend to IncrementalMerkleTreeContainer.append with reused nullifier
- Attacker controls: request/transaction/contract inputs to `IncrementalMerkleTreeContainer.append` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through IncrementalMerkleTreeContainer.append to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in IncrementalMerkleTreeContainer.append
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
