# Q0549: SVM revert data - mode selection mode switch after sign

## Question
Can an unprivileged attacker create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline and use control over recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID so that `buildRevertData` change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, breaking the invariant that the TSS message commits to exactly one Solana economic action, accounts set, and deadline and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
