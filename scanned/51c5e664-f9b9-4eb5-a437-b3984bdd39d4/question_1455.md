# Q1455: Session outbound verify - deadline/expiry verification split

## Question
When an unprivileged actor submit many public Push-chain actions that create concurrent outbounds to the same destination chain, does `verifyOutboundSigningRequest` remain safe if they control signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast, or can that make it make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:verifyOutboundSigningRequest
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
