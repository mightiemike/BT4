# Q1894: Event dedupe insert - payload row race overwrite

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `InsertEventIfNotExists` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, so that one user-visible bridge action can have at most one authoritative live row at a time no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
