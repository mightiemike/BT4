# Q3839: SVM revert data - account material hash semantic split

## Question
Can an unprivileged attacker trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message and use control over accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes so that `buildRevertData` make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS, breaking the invariant that the TSS message commits to exactly one Solana economic action, accounts set, and deadline and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes
- Exploit idea: make two economically different Solana outbounds produce the same or equivalent signed meaning to TSS
- Invariant to test: the TSS message commits to exactly one Solana economic action, accounts set, and deadline
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: reconstruct the TSS message, final Solana instructions, and any ref-route transactions on a local validator and diff them under edge-case fields
