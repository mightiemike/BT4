# Q3369: SVM revert data - id padding resource amplification

## Question
If a user trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message, can `buildRevertData` be pushed into a path where `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message causes it to turn one user outbound into excessive transaction, compute-unit, PDA, or ALT work that blocks other outbounds from finalizing, so that signing, broadcast, and resolution all agree on one execution mode for the outbound no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message
- Exploit idea: turn one user outbound into excessive transaction, compute-unit, PDA, or ALT work that blocks other outbounds from finalizing
- Invariant to test: signing, broadcast, and resolution all agree on one execution mode for the outbound
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: vary ID lengths, leading zeros, and payload formats and verify distinct outbounds cannot collapse to the same signed meaning
