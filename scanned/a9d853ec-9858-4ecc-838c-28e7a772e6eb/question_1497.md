# Q1497: SVM executed-PDA check - reclaimer age stalled resolver

## Question
If a user create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, can `IsAlreadyExecuted` be pushed into a path where orphan discovery inputs and age checks for PDAs created by attacker-controlled ref-route payloads causes it to keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, so that refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: orphan discovery inputs and age checks for PDAs created by attacker-controlled ref-route payloads
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: vary cluster time, delay finality, and hold transactions near the deadline to see whether `resolveSVM` refunds too early or too late
