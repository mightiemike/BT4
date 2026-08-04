# Q1455: merkle-anchor mismatch in IncrementalMerkleTreeCapsule.getLeft

## Question
Can an unprivileged attacker make /wallet/createshieldedcontractparameters feed chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleTreeCapsule.java::getLeft a stale or mismatched Merkle root, voucher, or anchor so spend validity is checked against one tree while settlement touches another, causing Double spend of one shielded note or withdrawal?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleTreeCapsule.java::getLeft
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Probe boundary blocks, changing anchors, mixed tree versions, and helper APIs that prepare spends or trigger inputs from historical state.
- Invariant to test: The committed tree root/anchor used for verification must be the exact one consumed by settlement and nullifier recording.
- Expected Immunefi impact: Double spend of one shielded note or withdrawal
- Fast validation: Create spends across anchor boundaries via /wallet/createshieldedcontractparameters; assert the verified anchor and the committed anchor always match.
