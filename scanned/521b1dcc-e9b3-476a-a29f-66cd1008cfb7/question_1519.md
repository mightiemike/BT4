# Q1519: Event data transition - event identity race overwrite

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `UpdateStatusAndEventData` be pushed into a path where `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data causes it to overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, so that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:UpdateStatusAndEventData
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
