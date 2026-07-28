# Q0745: SVM executed-PDA check - executed-pda state stalled resolver

## Question
Can an unprivileged attacker create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline and use control over the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs so that `IsAlreadyExecuted` keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, breaking the invariant that stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
