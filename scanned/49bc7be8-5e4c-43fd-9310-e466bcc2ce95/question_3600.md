# Q3600: Signer outbound wrapper - authz wrap hash/content split

## Question
When an unprivileged actor cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts, does `VoteOutbound` remain safe if they control the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction, or can that make it record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, violate the rule that retrying a vote never changes the meaning or terminal outcome of the economic action, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: retrying a vote never changes the meaning or terminal outcome of the economic action
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: replay the same source event or pending outbound and verify the signer cannot emit multiple economically distinct votes
