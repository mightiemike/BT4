# Q0017: Event cleanup delete - event identity race overwrite

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `DeleteTerminalEvents` remain safe if they control `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data, or can that make it overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, violate the rule that one user-visible bridge action can have at most one authoritative live row at a time, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
