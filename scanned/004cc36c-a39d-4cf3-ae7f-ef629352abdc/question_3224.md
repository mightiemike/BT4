# Q3224: Signer outbound wrapper - vote contents hash/content split

## Question
When an unprivileged actor cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts, does `VoteOutbound` remain safe if they control the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`, or can that make it record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, violate the rule that every signed vote exactly matches the source event or pending outbound that triggered it, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts
- Attacker controls: the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: every signed vote exactly matches the source event or pending outbound that triggered it
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: log the exact protobuf vote message before AuthZ wrapping and compare it against the raw event or outbound fields under attacker-controlled inputs
