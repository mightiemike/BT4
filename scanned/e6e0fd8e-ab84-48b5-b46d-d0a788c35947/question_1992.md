# Q1992: Inbound build - payload row dedupe bypass

## Question
When an unprivileged actor create a public Push-chain action that produces a pending outbound observed by the Universal Client, does `constructInbound` remain safe if they control the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic, or can that make it bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
