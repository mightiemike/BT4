# Q0404: Signer outbound wrapper - authz wrap wrong vote payload

## Question
When an unprivileged actor submit a public source-chain transfer that reaches the inbound vote path, does `VoteOutbound` remain safe if they control the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction, or can that make it sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row, violate the rule that retrying a vote never changes the meaning or terminal outcome of the economic action, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: submit a public source-chain transfer that reaches the inbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row
- Invariant to test: retrying a vote never changes the meaning or terminal outcome of the economic action
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: log the exact protobuf vote message before AuthZ wrapping and compare it against the raw event or outbound fields under attacker-controlled inputs
