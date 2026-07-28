# Q0327: Session outbound verify - nonce assignment verification split

## Question
When an unprivileged actor submit many public Push-chain actions that create concurrent outbounds to the same destination chain, does `verifyOutboundSigningRequest` remain safe if they control chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing, or can that make it make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, violate the rule that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:verifyOutboundSigningRequest
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
