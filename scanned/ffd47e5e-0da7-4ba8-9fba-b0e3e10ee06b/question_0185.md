# Q0185: SVM resolve path - deadline clock duplicate execution

## Question
When an unprivileged actor create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline, does `resolveSVM` remain safe if they control cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`, or can that make it rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk, violate the rule that each outbound has one terminal economic path rather than both execution and refund, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: create a public Solana outbound that lands slowly or stays unexecuted near its signing deadline
- Attacker controls: cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`
- Exploit idea: rebroadcast or re-drive a Solana outbound after it is already economically decided, causing double execution risk
- Invariant to test: each outbound has one terminal economic path rather than both execution and refund
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: create ref-route outbounds, delay execution, and verify the rent reclaimer never closes PDAs still required for broadcast or resolution
