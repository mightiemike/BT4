# Q1475: value-fee boundary bug in IncrementalMerkleVoucherCapsule.clearCursor

## Question
Can an unprivileged attacker send boundary note values, fees, or mixed transparent/shielded amounts through /wallet/createshieldedcontractparameters so chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleVoucherCapsule.java::clearCursor misprices or misbalances the flow and reaches Unauthorized shielded spend or note theft?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleVoucherCapsule.java::clearCursor
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Stress min/max note values, zero-like notes, fee boundaries, and mixed transparent/shielded amount distributions.
- Invariant to test: Shielded value, transparent value, and fees must conserve exactly across every accepted boundary case.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Boundary-fuzz all numeric shielded fields through /wallet/createshieldedcontractparameters; assert exact conservation and expected rejections.
