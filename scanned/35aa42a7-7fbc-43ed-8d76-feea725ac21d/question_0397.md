# Q0397: Event cleaner pass - payload row race overwrite

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic so that `performCleanup` overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, breaking the invariant that restarts and retries do not change the economic meaning of an event that is already in flight and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
