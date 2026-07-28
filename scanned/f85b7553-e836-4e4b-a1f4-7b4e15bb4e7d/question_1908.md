# Q1908: Signer outbound wrapper - authz wrap wrong vote payload

## Question
When an unprivileged actor create a public Push-chain outbound that reaches the outbound vote path, does `VoteOutbound` remain safe if they control the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction, or can that make it sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row, violate the rule that every signed vote exactly matches the source event or pending outbound that triggered it, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: create a public Push-chain outbound that reaches the outbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row
- Invariant to test: every signed vote exactly matches the source event or pending outbound that triggered it
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: capture tx bytes, on-chain message contents, and local `vote_tx_hash` values to confirm they always stay aligned
