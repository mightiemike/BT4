# Q0590: Push inbound vote msg - authz wrap hash/content split

## Question
If a user submit a public source-chain transfer that reaches the inbound vote path, can `voteInbound` be pushed into a path where the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction causes it to record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, so that one economic bridge action results in at most one effective vote path per validator no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/vote.go:voteInbound
- Entrypoint: submit a public source-chain transfer that reaches the inbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: one economic bridge action results in at most one effective vote path per validator
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: capture tx bytes, on-chain message contents, and local `vote_tx_hash` values to confirm they always stay aligned
