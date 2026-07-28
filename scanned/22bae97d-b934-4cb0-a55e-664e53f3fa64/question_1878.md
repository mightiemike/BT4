# Q1878: SVM orphan discovery - deadline clock stalled resolver

## Question
When an unprivileged actor submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, does `discoverOrphans` remain safe if they control cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`, or can that make it keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, violate the rule that refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/rent_reclaimer.go:discoverOrphans
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: cluster block time, signing deadline, revert slack, and host-wall-clock interactions used by `resolveSVM`
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: refund or revert decisions happen only after reliable proof the intended Solana execution will not succeed
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force repeated not-found and delayed-confirmation cases and ensure the same outbound cannot both execute and refund
