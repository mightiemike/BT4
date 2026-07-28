# Q3518: Coordinator sign setup - sign setup data cross-event nonce reuse

## Question
Can an unprivileged attacker start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED` and use control over the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data so that `createSignSetup` cause one outbound to reuse or consume signing state that should belong to a different outbound, breaking the invariant that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
