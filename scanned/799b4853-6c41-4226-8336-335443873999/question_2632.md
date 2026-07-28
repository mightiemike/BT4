# Q2632: SVM deadline read - broadcast record stalled resolver

## Question
If a user submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, can `ReadSigningDeadline` be pushed into a path where persisted tx hash, signed bytes, and broadcaster state for the outbound causes it to keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, so that stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSigningDeadline
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: persisted tx hash, signed bytes, and broadcaster state for the outbound
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: stored ix-data and executed-state PDAs needed by a live outbound are never reclaimed early
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: vary cluster time, delay finality, and hold transactions near the deadline to see whether `resolveSVM` refunds too early or too late
