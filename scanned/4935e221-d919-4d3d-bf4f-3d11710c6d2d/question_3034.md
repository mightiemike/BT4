# Q3034: Push inbound vote msg - vote contents wrong vote payload

## Question
When an unprivileged actor cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts, does `voteInbound` remain safe if they control the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`, or can that make it sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row, violate the rule that the stored vote hash always corresponds to the payload and status the client believes it submitted, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/vote.go:voteInbound
- Entrypoint: cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts
- Attacker controls: the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`
- Exploit idea: sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row
- Invariant to test: the stored vote hash always corresponds to the payload and status the client believes it submitted
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: capture tx bytes, on-chain message contents, and local `vote_tx_hash` values to confirm they always stay aligned
