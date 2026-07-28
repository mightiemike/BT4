# Q0463: SVM executed-PDA check - executed-pda state false revert

## Question
If a user create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, can `IsAlreadyExecuted` be pushed into a path where the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs causes it to vote a Solana outbound as failed and refund it even though the original execution can still complete or already completed, so that normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: vote a Solana outbound as failed and refund it even though the original execution can still complete or already completed
- Invariant to test: normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one outbound through broadcast, resolve, and cleanup until terminal and confirm the state machine always converges
