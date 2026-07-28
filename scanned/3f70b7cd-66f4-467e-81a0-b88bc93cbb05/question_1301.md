# Q1301: SVM revert data - time/value fields mode switch after sign

## Question
When an unprivileged actor create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline, does `buildRevertData` remain safe if they control amount, gas fee, signing deadline, and relayer fee assumptions carried into the signed message and built transactions, or can that make it change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, violate the rule that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline
- Attacker controls: amount, gas fee, signing deadline, and relayer fee assumptions carried into the signed message and built transactions
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
