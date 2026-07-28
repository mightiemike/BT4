# Q2002: Signer outbound wrapper - authz wrap duplicate vote attempt

## Question
Can an unprivileged attacker create a public Push-chain outbound that reaches the outbound vote path and use control over the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction so that `VoteOutbound` reissue votes for the same economic action in a way that causes double execution, early completion, or queue deadlock, breaking the invariant that one economic bridge action results in at most one effective vote path per validator and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: create a public Push-chain outbound that reaches the outbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: reissue votes for the same economic action in a way that causes double execution, early completion, or queue deadlock
- Invariant to test: one economic bridge action results in at most one effective vote path per validator
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: replay the same source event or pending outbound and verify the signer cannot emit multiple economically distinct votes
