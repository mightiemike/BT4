# Q1459: proof-decrypt underpricing in IncrementalMerkleTreeCapsule.clearLeft

## Question
Can an unprivileged attacker spam /wallet/createshieldedcontractparameters so chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleTreeCapsule.java::clearLeft performs materially underpriced proof verification, decryption, hashing, or Merkle work on public input and causes Materially underpriced public proof, decrypt, or note-scan work?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleTreeCapsule.java::clearLeft
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Use large proof sets, note windows, malformed-but-decodable blobs, and repeated requests that redo expensive verification or decryption.
- Invariant to test: Public cryptographic helper work must be bounded and proportionate to the cost or limits visible to the attacker.
- Expected Immunefi impact: Materially underpriced public proof, decrypt, or note-scan work
- Fast validation: Benchmark worst valid and near-valid inputs via /wallet/createshieldedcontractparameters; flag cases where attacker-controlled cryptographic work scales far faster than request cost.
