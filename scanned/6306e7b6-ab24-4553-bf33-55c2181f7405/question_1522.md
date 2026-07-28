# Q1522: Inbound build - event identity race overwrite

## Question
When an unprivileged actor create a public Push-chain action that produces a pending outbound observed by the Universal Client, does `constructInbound` remain safe if they control `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data, or can that make it overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
