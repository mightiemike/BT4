# Q3904: transparent-shielded accounting drift in ZenTransactionBuilder.addOutput

## Question
Can an unprivileged attacker use /wallet/createshieldedcontractparameters so framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java::addOutput moves value between transparent and shielded state with inconsistent fee or amount handling, making the nullifier or anchor state and shielded note value, transparent balances, or note-spent status diverge and leading to Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java::addOutput
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Focus on transparent-from, transparent-to, fee, and note-value interactions, especially when some branches are optional.
- Invariant to test: Transfers between transparent and shielded representations must conserve value exactly except for the intended fee burn.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Fuzz every combination of transparent/shielded inputs through /wallet/createshieldedcontractparameters; assert net value conservation across both representations plus fees.
