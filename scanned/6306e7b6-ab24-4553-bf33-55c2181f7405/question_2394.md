# Q2394: Session create - session persistence cross-event nonce reuse

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `createSession` remain safe if they control persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry, or can that make it cause one outbound to reuse or consume signing state that should belong to a different outbound, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:createSession
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
