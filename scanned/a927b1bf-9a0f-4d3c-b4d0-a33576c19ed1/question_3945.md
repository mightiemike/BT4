# Q3945: SVM resolve path - broadcast record duplicate execution

## Question
When an unprivileged actor trigger large Solana outbounds that leave stored ix-data PDAs and then wait for reclaimer cleanup windows, does `resolveSVM` remain safe if they control persisted tx hash, signed bytes, and broadcaster state for the outbound, or can that make it rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: trigger large Solana outbounds that leave stored ix-data PDAs and then wait for reclaimer cleanup windows
- Attacker controls: persisted tx hash, signed bytes, and broadcaster state for the outbound
- Exploit idea: rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
