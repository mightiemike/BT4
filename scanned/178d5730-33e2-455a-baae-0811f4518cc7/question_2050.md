# Q2050: SVM TSS bind - mode selection mode switch after sign

## Question
If a user cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload, can `constructTSSMessage` be pushed into a path where recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID causes it to change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, so that signing, broadcast, and resolution all agree on one execution mode for the outbound no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:constructTSSMessage
- Entrypoint: cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: signing, broadcast, and resolution all agree on one execution mode for the outbound
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: vary ID lengths, leading zeros, and payload formats and verify distinct outbounds cannot collapse to the same signed meaning
