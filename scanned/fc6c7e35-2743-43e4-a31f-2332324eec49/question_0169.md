# Q0169: SVM ref-route build - id padding mode switch after sign

## Question
When an unprivileged actor create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline, does `BuildRefRouteTransactions` remain safe if they control `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message, or can that make it change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, violate the rule that signing, broadcast, and resolution all agree on one execution mode for the outbound, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:BuildRefRouteTransactions
- Entrypoint: create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline
- Attacker controls: `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: signing, broadcast, and resolution all agree on one execution mode for the outbound
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: vary ID lengths, leading zeros, and payload formats and verify distinct outbounds cannot collapse to the same signed meaning
