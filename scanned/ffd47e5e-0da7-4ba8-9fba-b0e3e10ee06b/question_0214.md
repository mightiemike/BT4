# Q0214: Push inbound vote msg - vote contents hash/content split

## Question
When an unprivileged actor submit a public source-chain transfer that reaches the inbound vote path, does `voteInbound` remain safe if they control the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`, or can that make it record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted, violate the rule that the stored vote hash always corresponds to the payload and status the client believes it submitted, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/pushsigner/vote.go:voteInbound
- Entrypoint: submit a public source-chain transfer that reaches the inbound vote path
- Attacker controls: the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`
- Exploit idea: record a `vote_tx_hash` for one vote while later logic assumes a different payload was voted
- Invariant to test: the stored vote hash always corresponds to the payload and status the client believes it submitted
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: replay the same source event or pending outbound and verify the signer cannot emit multiple economically distinct votes
