# Q3412: Signer outbound wrapper - authz wrap wrong vote payload

## Question
Can an unprivileged attacker cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts and use control over the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction so that `VoteOutbound` sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row, breaking the invariant that one economic bridge action results in at most one effective vote path per validator and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: sign and broadcast a vote whose economic meaning differs from the attacker-controlled source event or outbound row
- Invariant to test: one economic bridge action results in at most one effective vote path per validator
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force signer retries and check whether account sequence or confirmation polling can mark the wrong event as completed
