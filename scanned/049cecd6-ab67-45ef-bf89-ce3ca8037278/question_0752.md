# Q0752: SVM deadline read - executed-pda state stalled resolver

## Question
If a user create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, can `ReadSigningDeadline` be pushed into a path where the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs causes it to keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, so that stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSigningDeadline
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
