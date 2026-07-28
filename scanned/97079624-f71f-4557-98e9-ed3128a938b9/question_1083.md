# Q1083: Eventstore stale unsigned cleanup - session persistence verification split

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `DeleteOldUnsignedEvents` be pushed into a path where persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry causes it to make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, so that nonce, signature, and eventstore state always belong to exactly one outbound at a time no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/eventstore/store.go:DeleteOldUnsignedEvents
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
