# Q0593: AuthZ vote assembly - authz wrap hash/content split

## Question
When an unprivileged actor submit a public source-chain transfer that reaches the inbound vote path, does `signAndBroadcastAuthZTx` remain safe if they control the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction, or can that make it record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, violate the rule that one economic bridge action results in at most one effective vote path per validator, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:signAndBroadcastAuthZTx
- Entrypoint: submit a public source-chain transfer that reaches the inbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: one economic bridge action results in at most one effective vote path per validator
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: capture tx bytes, on-chain message contents, and local `vote_tx_hash` values to confirm they always stay aligned
