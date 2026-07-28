# Q2206: Session create - sign setup data verification split

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `createSession` remain safe if they control the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data, or can that make it make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, violate the rule that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:createSession
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
