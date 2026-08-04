# Q3922: shielded replay window in DiversifierT.random

## Question
Can an unprivileged attacker repeat or reorder note-scan, note-marking, spend, or withdraw flows around /wallet/scanshieldedtrc20notesbyivk so framework/src/main/java/org/tron/core/zen/address/DiversifierT.java::random observes stale the nullifier or anchor state/shielded note value, transparent balances, or note-spent status and accepts a logical replay, resulting in Double spend of one shielded note or withdrawal?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/DiversifierT.java::random
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Mix note status queries, spend construction, and repeated broadcasts around moving anchors or spent-state updates.
- Invariant to test: Shielded note status seen by public helpers must remain consistent with the later spend gate and must not create a replay window.
- Expected Immunefi impact: Double spend of one shielded note or withdrawal
- Fast validation: Interleave note queries/builds with repeated spends via /wallet/scanshieldedtrc20notesbyivk; assert the first success closes every equivalent replay path immediately.
