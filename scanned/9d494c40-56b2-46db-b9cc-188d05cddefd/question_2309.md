# Q2309: IncrementalMerkleTreeContainer: nullifier/anchor reuse

## Question
Can an unprivileged attacker (shielded transaction) abuse `IncrementalMerkleTreeContainer.wfcheck` in `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` — where the attacker replays a nullifier or stale anchor through IncrementalMerkleTreeContainer.wfcheck to double-spend a shielded note — to break the invariant that each nullifier is accepted once and anchors must be current in IncrementalMerkleTreeContainer.wfcheck, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java` -> `IncrementalMerkleTreeContainer.wfcheck`
- Entrypoint: shielded spend to IncrementalMerkleTreeContainer.wfcheck with reused nullifier
- Attacker controls: request/transaction/contract inputs to `IncrementalMerkleTreeContainer.wfcheck` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a nullifier or stale anchor through IncrementalMerkleTreeContainer.wfcheck to double-spend a shielded note
- Invariant to test: each nullifier is accepted once and anchors must be current in IncrementalMerkleTreeContainer.wfcheck
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit replaying nullifier asserting rejection
