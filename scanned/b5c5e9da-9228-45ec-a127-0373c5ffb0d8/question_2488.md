# Q2488: Session create - session persistence recovered double-sign

## Question
Can an unprivileged attacker create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls and use control over persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry so that `createSession` recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, breaking the invariant that nonce, signature, and eventstore state always belong to exactly one outbound at a time and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:createSession
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
