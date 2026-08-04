# Q1173: builder-executor mismatch in IncrementalMerkleTreeContainer.wfcheck

## Question
Can an unprivileged attacker use /wallet/createshieldedcontractparameters so chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java::wfcheck builds shielded parameters under assumptions that the later executor does not recheck, allowing a crafted request to reach Unauthorized shielded spend or note theft?

## Target
- File/function: chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java::wfcheck
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Compare parameter builders, trigger-input helpers, note scans, and final settlement for missing consistency checks.
- Invariant to test: Any helper that prepares a shielded action must enforce the same object identity, amount, and context rules as final settlement.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Build one shielded action through all public helper APIs via /wallet/createshieldedcontractparameters; assert the executor revalidates every security-critical field.
