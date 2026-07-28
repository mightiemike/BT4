# Q3319: AuthZ vote assembly - vote contents retry desync

## Question
When an unprivileged actor cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts, does `signAndBroadcastAuthZTx` remain safe if they control the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`, or can that make it desynchronize local completion from on-chain vote success so the event is lost, replayed, or mis-resolved, violate the rule that one economic bridge action results in at most one effective vote path per validator, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/pushsigner/pushsigner.go:signAndBroadcastAuthZTx
- Entrypoint: cause a user-reachable event to be retried until the signer wraps and broadcasts multiple AuthZ vote attempts
- Attacker controls: the decoded fields used to build `MsgVoteInbound` or `MsgVoteOutbound`
- Exploit idea: desynchronize local completion from on-chain vote success so the event is lost, replayed, or mis-resolved
- Invariant to test: one economic bridge action results in at most one effective vote path per validator
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force signer retries and check whether account sequence or confirmation polling can mark the wrong event as completed
