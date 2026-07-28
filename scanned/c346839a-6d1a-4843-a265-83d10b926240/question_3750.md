# Q3750: SVM ALT fetch - mode selection resource amplification

## Question
If a user trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message, can `fetchAddressTables` be pushed into a path where recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID causes it to turn one user outbound into excessive transaction, compute-unit, PDA, or ALT work that blocks other outbounds from finalizing, so that the TSS message commits to exactly one Solana economic action, accounts set, and deadline no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:fetchAddressTables
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: turn one user outbound into excessive transaction, compute-unit, PDA, or ALT work that blocks other outbounds from finalizing
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
