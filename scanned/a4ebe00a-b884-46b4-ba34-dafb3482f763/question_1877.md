# Q1877: SVM resolve path - deadline clock stalled resolver

## Question
If a user submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, can `resolveSVM` be pushed into a path where cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM` causes it to keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, so that refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txresolver/svm.go:resolveSVM
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
