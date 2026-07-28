# Q2094: Push inbound vote msg - authz wrap hash/content split

## Question
When an unprivileged actor create a public Push-chain outbound that reaches the outbound vote path, does `voteInbound` remain safe if they control the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction, or can that make it record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, violate the rule that the stored vote hash always corresponds to the payload and status the client believes it submitted, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/pushsigner/vote.go:voteInbound
- Entrypoint: create a public Push-chain outbound that reaches the outbound vote path
- Attacker controls: the `sdk.Msg` list ultimately wrapped into an AuthZ exec transaction
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: the stored vote hash always corresponds to the payload and status the client believes it submitted
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: log the exact protobuf vote message before AuthZ wrapping and compare it against the raw event or outbound fields under attacker-controlled inputs
