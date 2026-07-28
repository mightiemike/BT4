# Q3225: AuthZ vote assembly - vote contents hash/content split

## Question
If a user cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts, can `signAndBroadcastAuthZTx` be pushed into a path where the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound` causes it to record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, so that every signed vote exactly matches the source event or pending outbound that triggered it no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:signAndBroadcastAuthZTx
- Entrypoint: cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts
- Attacker controls: the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: every signed vote exactly matches the source event or pending outbound that triggered it
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: log the exact protobuf vote message before AuthZ wrapping and compare it against the raw event or outbound fields under attacker-controlled inputs
