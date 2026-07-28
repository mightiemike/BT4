# Q0703: Session outbound verify - sign setup data verification split

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `verifyOutboundSigningRequest` be pushed into a path where the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data causes it to make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:verifyOutboundSigningRequest
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
