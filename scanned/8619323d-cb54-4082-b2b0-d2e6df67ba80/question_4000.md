# Q4000: transparent-shielded accounting drift in Note.decrypt

## Question
Can an unprivileged attacker use /wallet/scanshieldedtrc20notesbyivk so framework/src/main/java/org/tron/core/zen/note/Note.java::decrypt moves value between transparent and shielded state with inconsistent fee or amount handling, making the nullifier or anchor state and shielded note value, transparent balances, or note-spent status diverge and leading to Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/note/Note.java::decrypt
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Focus on transparent-from, transparent-to, fee, and note-value interactions, especially when some branches are optional.
- Invariant to test: Transfers between transparent and shielded representations must conserve value exactly except for the intended fee burn.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Fuzz every combination of transparent/shielded inputs through /wallet/scanshieldedtrc20notesbyivk; assert net value conservation across both representations plus fees.
