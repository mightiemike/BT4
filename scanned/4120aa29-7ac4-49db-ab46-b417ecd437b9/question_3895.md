# Q3895: proof-decrypt underpricing in ShieldedTRC20ParametersBuilder.createSpendAuth

## Question
Can an unprivileged attacker spam shielded transaction build -> sign -> /wallet/broadcasttransaction so framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java::createSpendAuth performs materially underpriced proof verification, decryption, hashing, or Merkle work on public input and causes Materially underpriced public proof, decrypt, or note-scan work?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java::createSpendAuth
- Entrypoint: shielded transaction build -> sign -> /wallet/broadcasttransaction
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Use large proof sets, note windows, malformed-but-decodable blobs, and repeated requests that redo expensive verification or decryption.
- Invariant to test: Public cryptographic helper work must be bounded and proportionate to the cost or limits visible to the attacker.
- Expected Immunefi impact: Materially underpriced public proof, decrypt, or note-scan work
- Fast validation: Benchmark worst valid and near-valid inputs via shielded transaction build -> sign -> /wallet/broadcasttransaction; flag cases where attacker-controlled cryptographic work scales far faster than request cost.
