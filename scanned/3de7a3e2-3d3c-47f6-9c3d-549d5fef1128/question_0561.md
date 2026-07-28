# Q0561: SVM resolve path - executed-pda state duplicate execution

## Question
If a user create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, can `resolveSVM` be pushed into a path where the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs causes it to rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk, so that refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk
- Invariant to test: refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: vary cluster time, delay finality, and hold transactions near the deadline to see whether `resolveSVM` refunds too early or too late
