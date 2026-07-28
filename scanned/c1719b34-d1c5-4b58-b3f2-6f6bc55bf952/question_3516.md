# Q3516: Coordinator event intake - sign setup data cross-event nonce reuse

## Question
When an unprivileged actor start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, does `processConfirmedEvents` remain safe if they control the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data, or can that make it cause one outbound to reuse or consume signing state that should belong to a different outbound, violate the rule that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:processConfirmedEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
