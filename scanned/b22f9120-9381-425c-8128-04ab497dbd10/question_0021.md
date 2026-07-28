# Q0021: Event cleaner pass - event identity race overwrite

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data so that `performCleanup` overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, breaking the invariant that one user-visible bridge action can have at most one authoritative live row at a time and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
