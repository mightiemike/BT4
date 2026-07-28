# Q0655: SVM resolve path - executed-pda state live-data deletion

## Question
When an unprivileged actor create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, does `resolveSVM` remain safe if they control the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs, or can that make it close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: close or reclaim stored ix-data that is still needed for a live outbound, permanently locking that user flow
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create ref-route outbounds, delay execution, and verify the rent reclaimer never closes PDAs still required for broadcast or resolution
