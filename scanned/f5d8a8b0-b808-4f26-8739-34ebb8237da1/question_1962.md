# Q1962: SVM stored-ix bytes - mode selection hash semantic split

## Question
If a user cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload, can `buildStoreIxDataData` be pushed into a path where recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID causes it to make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS, so that the TSS message commits to exactly one Solana economic action, accounts set, and deadline no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildStoreIxDataData
- Entrypoint: cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
