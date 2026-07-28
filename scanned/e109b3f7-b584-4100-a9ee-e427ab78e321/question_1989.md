# Q1989: Event data transition - payload row dedupe bypass

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `UpdateStatusAndEventData` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, so that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:UpdateStatusAndEventData
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
