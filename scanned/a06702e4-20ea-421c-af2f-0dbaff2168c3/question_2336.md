# Q2336: false-spent partial write in IncrementalMerkleTreeStore.contain

## Question
Can an unprivileged attacker cause /wallet/createshieldedcontractparameters to make chainbase/src/main/java/org/tron/core/store/IncrementalMerkleTreeStore.java::contain mark a note or spend object in the nullifier or anchor state before all later checks succeed, so the user’s value becomes unrecoverable in shielded note value, transparent balances, or note-spent status and the final outcome is Permanent false-spent state or irrecoverable shielded-note lock?

## Target
- File/function: chainbase/src/main/java/org/tron/core/store/IncrementalMerkleTreeStore.java::contain
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Target flows where note-spent markers, nullifiers, or anchor-linked records are written before final proof and accounting completion.
- Invariant to test: Shielded state must only advance to spent/final after every proof, amount, and accounting check has succeeded.
- Expected Immunefi impact: Permanent false-spent state or irrecoverable shielded-note lock
- Fast validation: Inject failures after each shielded-state write via /wallet/createshieldedcontractparameters; assert no valid note becomes falsely spent or unrecoverable.
