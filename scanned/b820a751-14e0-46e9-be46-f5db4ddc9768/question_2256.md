# Q2256: SVM deadline read - executed-pda state stalled resolver

## Question
When an unprivileged actor submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC, does `ReadSigningDeadline` remain safe if they control the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs, or can that make it keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness, violate the rule that normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSigningDeadline
- Entrypoint: submit a public outbound whose broadcasted Solana signature is temporarily absent or delayed at RPC
- Attacker controls: the presence or absence of the `ExecutedTx` PDA and any stored ix-data PDAs derived from attacker-controlled IDs
- Exploit idea: keep one or more user outbounds forever nonterminal because the resolver, broadcaster, and cleanup logic disagree about liveness
- Invariant to test: normal Solana outbounds eventually reach a correct terminal state even across delayed finality and cleanup activity
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: create ref-route outbounds, delay execution, and verify the rent reclaimer never closes PDAs still required for broadcast or resolution
