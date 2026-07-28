# Q0831: SVM revert data - account material hash semantic split

## Question
When an unprivileged actor create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline, does `buildRevertData` remain safe if they control accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes, or can that make it make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS, violate the rule that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: create a public Push-chain outbound to Solana with attacker-chosen IDs, recipient, amount, asset, payload, gas fee, and deadline
- Attacker controls: accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes
- Exploit idea: make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
