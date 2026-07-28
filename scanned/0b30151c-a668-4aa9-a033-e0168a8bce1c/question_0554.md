# Q0554: SVM ALT fetch - mode selection mode switch after sign

## Question
If a user create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline, can `fetchAddressTables` be pushed into a path where recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID causes it to change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, so that the TSS message commits to exactly one Solana economic action, accounts set, and deadline no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:fetchAddressTables
- Entrypoint: create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
