# Q1121: SVM executed-PDA check - broadcast record stalled resolver

## Question
When an unprivileged actor create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, does `IsAlreadyExecuted` remain safe if they control persisted tx hash, signed bytes, and broadcaster state for the outbound, or can that make it keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: persisted tx hash, signed bytes, and broadcaster state for the outbound
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create ref-route outbounds, delay execution, and verify the rent reclaimer never closes PDAs still required for broadcast or resolution
