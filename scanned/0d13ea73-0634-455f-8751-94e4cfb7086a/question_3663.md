# Q3663: SVM resolve path - executed-pda state live-data deletion

## Question
If a user trigger large Solana outbounds that leave stored ix-data PDAs and then wait for reclaimer cleanup windows, can `resolveSVM` be pushed into a path where the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs causes it to close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow, so that normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: trigger large Solana outbounds that leave stored ix-data PDAs and then wait for reclaimer cleanup windows
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow
- Invariant to test: normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: vary cluster time, delay finality, and hold transactions near the deadline to see whether `resolveSVM` refunds too early or too late
