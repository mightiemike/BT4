# Q3562: SVM ALT fetch - mode selection mode switch after sign

## Question
Can an unprivileged attacker trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message and use control over recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID so that `fetchAddressTables` change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, breaking the invariant that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:fetchAddressTables
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
