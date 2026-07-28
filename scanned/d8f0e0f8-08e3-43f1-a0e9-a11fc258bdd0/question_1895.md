# Q1895: Event data transition - payload row race overwrite

## Question
Can an unprivileged attacker create a public Push-chain action that produces a pending outbound observed by the Universal Client and use control over the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic so that `UpdateStatusAndEventData` overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, breaking the invariant that one user-visible bridge action can have at most one authoritative live row at a time and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:UpdateStatusAndEventData
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
