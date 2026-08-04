# Q3980: false-spent partial write in PaymentAddress.decode

## Question
Can an unprivileged attacker cause /wallet/scanshieldedtrc20notesbyovk to make framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode mark a note or spend object in the nullifier or anchor state before all later checks succeed, so the user’s value becomes unrecoverable in shielded note value, transparent balances, or note-spent status and the final outcome is Permanent false-spent state or irrecoverable shielded-note lock?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode
- Entrypoint: /wallet/scanshieldedtrc20notesbyovk
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Target flows where note-spent markers, nullifiers, or anchor-linked records are written before final proof and accounting completion.
- Invariant to test: Shielded state must only advance to spent/final after every proof, amount, and accounting check has succeeded.
- Expected Immunefi impact: Permanent false-spent state or irrecoverable shielded-note lock
- Fast validation: Inject failures after each shielded-state write via /wallet/scanshieldedtrc20notesbyovk; assert no valid note becomes falsely spent or unrecoverable.
