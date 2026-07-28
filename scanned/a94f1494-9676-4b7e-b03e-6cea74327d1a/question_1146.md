# Q1146: Inbound build - cleanup horizon race overwrite

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `constructInbound` remain safe if they control the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried, or can that make it overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
