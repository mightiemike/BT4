# Q0369: SVM executed-PDA check - deadline clock stalled resolver

## Question
If a user create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, can `IsAlreadyExecuted` be pushed into a path where cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM` causes it to keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, so that normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one outbound through broadcast, resolve, and cleanup until terminal and confirm the state machine always converges
