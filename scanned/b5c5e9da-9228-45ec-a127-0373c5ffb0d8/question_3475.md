# Q3475: SVM resolve path - executed-pda state false revert

## Question
When an unprivileged actor trigger large Solana outbounds that leave stored ix-data PDAs and then wait for reclaimer cleanup windows, does `resolveSVM` remain safe if they control the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs, or can that make it vote a Solana outbound as failed and refund it even though the original execution can still complete or already completed, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: trigger large Solana outbounds that leave stored ix-data PDAs and then wait for reclaimer cleanup windows
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: vote a Solana outbound as failed and refund it even though the original execution can still complete or already completed
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
