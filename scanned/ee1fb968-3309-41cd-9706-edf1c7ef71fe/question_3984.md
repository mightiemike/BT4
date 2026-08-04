# Q3984: spent-status divergence in PaymentAddress.decode

## Question
Can an unprivileged attacker abuse /wallet/scanshieldedtrc20notesbyivk so framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode reports note-spent or note-available status from a different source or encoding than the later spend path uses, and chain that mismatch into Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Compare helper APIs that answer note status with the exact settlement path that will later consume that note.
- Invariant to test: Public spent-status answers must exactly match the canonical state the later executor uses for spend authorization.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Query note status via /wallet/scanshieldedtrc20notesbyivk, then immediately attempt every equivalent spend/withdraw path and assert the answer matches execution reality.
