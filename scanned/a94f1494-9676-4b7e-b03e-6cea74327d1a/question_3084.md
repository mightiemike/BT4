# Q3084: SVM TSS bind - id padding hash semantic split

## Question
When an unprivileged actor trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message, does `constructTSSMessage` remain safe if they control `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message, or can that make it make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS, violate the rule that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:constructTSSMessage
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: `TxID` and `UniversalTxId` width, zero-padding, and byte alignment before they are folded into the TSS message
- Exploit idea: make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
