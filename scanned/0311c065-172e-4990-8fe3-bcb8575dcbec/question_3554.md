# Q3554: SVM TSS bind - mode selection mode switch after sign

## Question
When an unprivileged actor trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message, does `constructTSSMessage` remain safe if they control recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID, or can that make it change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, violate the rule that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:constructTSSMessage
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
