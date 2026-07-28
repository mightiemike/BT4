# Q1489: SVM revert data - time/value fields resource amplification

## Question
Can an unprivileged attacker create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline and use control over amount, gas fee, signing deadline, and relayer fee assumptions carried into the signed message and built transactions so that `buildRevertData` turn one user outbound into excessive transaction, compute-unit, PDA, or ALT work that blocks other outbounds from finalizing, breaking the invariant that the TSS message commits to exactly one Solana economic action, accounts set, and deadline and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline
- Attacker controls: amount, gas fee, signing deadline, and relayer fee assumptions carried into the signed message and built transactions
- Exploit idea: turn one user outbound into excessive transaction, compute-unit, PDA, or ALT work that blocks other outbounds from finalizing
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
