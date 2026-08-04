# Q3976: transparent-shielded accounting drift in PaymentAddress.decode

## Question
Can an unprivileged attacker use /wallet/createshieldedcontractparameterswithoutask so framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode moves value between transparent and shielded state with inconsistent fee or amount handling, making the nullifier or anchor state and shielded note value, transparent balances, or note-spent status diverge and leading to Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode
- Entrypoint: /wallet/createshieldedcontractparameterswithoutask
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Focus on transparent-from, transparent-to, fee, and note-value interactions, especially when some branches are optional.
- Invariant to test: Transfers between transparent and shielded representations must conserve value exactly except for the intended fee burn.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Fuzz every combination of transparent/shielded inputs through /wallet/createshieldedcontractparameterswithoutask; assert net value conservation across both representations plus fees.
