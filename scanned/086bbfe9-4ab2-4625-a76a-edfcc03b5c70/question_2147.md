# Q2147: SVM revert data - mode selection stored-data collision

## Question
When an unprivileged actor cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload, does `buildRevertData` remain safe if they control recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID, or can that make it collide, overwrite, or misaddress stored ix-data so execution consumes the wrong bytes or permanently loses the right bytes, violate the rule that stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:buildRevertData
- Entrypoint: cause a public Push-chain flow to produce a Solana execute-style outbound with attacker-controlled accounts and ixData in the payload
- Attacker controls: recipient, target program, tx type, native-vs-SPL asset choice, and payload-derived instruction ID
- Exploit idea: collide, overwrite, or misaddress stored ix-data so execution consumes the wrong bytes or permanently loses the right bytes
- Invariant to test: stored ix-data and PDA derivations are unique to one outbound and cannot be confused with another
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force payloads over the inline limit and inspect whether stored ix-data PDAs and ref-route transactions remain unique and recoverable
