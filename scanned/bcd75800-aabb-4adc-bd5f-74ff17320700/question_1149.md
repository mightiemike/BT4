# Q1149: Event cleaner pass - cleanup horizon race overwrite

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `performCleanup` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, so that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
