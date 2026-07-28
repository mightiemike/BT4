# Q1710: Inbound build - event identity premature delete

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `constructInbound` be pushed into a path where `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that restarts and retries do not change the economic meaning of an event that is already in flight no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
