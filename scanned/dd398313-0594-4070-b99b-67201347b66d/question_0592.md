# Q0592: Signer outbound wrapper - authz wrap hash/content split

## Question
Can an unprivileged attacker submit a public source-chain transfer that reaches the inbound vote path and use control over the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction so that `VoteOutbound` record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, breaking the invariant that one economic bridge action results in at most one effective vote path per validator and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:VoteOutbound
- Entrypoint: submit a public source-chain transfer that reaches the inbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: one economic bridge action results in at most one effective vote path per validator
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: capture tx bytes, on-chain message contents, and local `vote_tx_hash` values to confirm they always stay aligned
