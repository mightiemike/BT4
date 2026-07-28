# Q0112: Inbound build - event identity dedupe bypass

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `constructInbound` remain safe if they control `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data, or can that make it bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
