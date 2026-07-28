# Q1906: Push inbound vote msg - authz wrap wrong vote payload

## Question
Can an unprivileged attacker create a public Push-chain outbound that reaches the outbound vote path and use control over the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction so that `voteInbound` sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row, breaking the invariant that every signed vote exactly matches the source event or pending outbound that triggered it and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/pushsigner/vote.go:voteInbound
- Entrypoint: create a public Push-chain outbound that reaches the outbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row
- Invariant to test: every signed vote exactly matches the source event or pending outbound that triggered it
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: capture tx bytes, on-chain message contents, and local `vote_tx_hash` values to confirm they always stay aligned
